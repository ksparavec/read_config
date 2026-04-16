#!/usr/bin/python
# -*- coding: utf-8 -*-
from __future__ import annotations

DOCUMENTATION = r'''
---
module: read_config
short_description: Recursively read and merge role configurations from pluggable storage backends.
description:
  - Merges hierarchical configuration data for a given role. The filesystem backend
    recursively scans a directory for YAML, JSON, or INI files named "<role_name>.<ext>"
    and produces one merged config per directory holding a matching file. Parent
    configs are merged first, then each child overrides along the path.
  - Additional backends (SQL, key-value stores, HTTP APIs, etc.) can be registered
    via C(read_config_core.registry.register_backend) and selected with the
    C(backend) option.
  - If no config_path is specified, multiple configs are returned in
    C(ansible_facts.read_config.configs). If config_path is specified, only that
    location's merged config is returned.
  - An optional parameter C(config_tag) filters out configs whose final merged
    data does not include a matching C(config_tag) key/value.
version_added: "2.0.0"
options:
  role_name:
    description:
      - The name of the role for which configuration files should be read.
      - Cannot contain path separators.
    type: str
    required: true
  config_dir:
    description:
      - Filesystem backend only. Top-level directory to search recursively for
        configuration files. Must exist and be readable. If omitted, the module
        attempts to locate the role's vars directory via C(ANSIBLE_CONFIG).
    type: path
    required: false
  config_path:
    description:
      - If specified, only return the merged config for that specific location
        (absolute or relative to config_dir for the filesystem backend). The
        target must be within the backend's root.
    type: str
    required: false
    default: null
  config_tag:
    description:
      - If specified, only return configs whose final merged data includes
        C(config_tag: <value>).
    type: str
    required: false
    default: null
  dry_run:
    description:
      - If true, report which sources would be merged without loading them.
    type: bool
    required: false
    default: false
  validate_schema:
    description:
      - Optional JSON schema file path to validate merged configurations against.
    type: str
    required: false
    default: null
  format:
    description:
      - Filesystem backend only. Format of the configuration files to read.
    type: str
    required: false
    default: yaml
    choices: [yaml, json, ini]
  track_changes:
    description:
      - Filesystem backend only. If true, track configuration changes between runs
        using a per-role checksum file in config_dir.
    type: bool
    required: false
    default: false
  backend:
    description:
      - Storage backend to use. Defaults to C(filesystem). Built-in backends
        are C(filesystem) and C(sql). Additional backends may be registered
        at runtime via C(read_config_core.registry.register_backend).
    type: str
    required: false
    default: filesystem
  backend_options:
    description:
      - Backend-specific options passed as keyword arguments to the backend
        factory. Required for non-filesystem backends (e.g. C(sql) needs C(dsn)).
      - Ignored by the filesystem backend; use C(config_dir) and C(format) instead.
    type: dict
    required: false
    default: null
author:
  - "Kresimir Sparavec (@ksparavec)"
'''

EXAMPLES = r'''
- name: Read all role configs for testrole from /path/to/config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs

- name: Read only subfolder2 config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: subfolder2
  register: single_config

- name: Read all configs but only those tagged 'production'
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_tag: production
  register: prod_configs
'''

RETURN = r'''
ansible_facts:
  description:
    - Returns a standardized structure with mode, configs, and matched_count.
    - When track_changes is enabled, includes changed_files list.
  type: dict
  returned: always
  sample:
    read_config:
      mode: multiple
      configs:
        "/absolute/path/to/config":
          meta:
            files_merged:
              - "/absolute/path/to/config/testrole.yaml"
          data:
            key1: val1
      matched_count: 1
changed:
  description:
    - Whether any configuration sources have changed since the last run.
    - Only set when track_changes is enabled.
  type: bool
  returned: when track_changes is true
  sample: true
'''

import configparser
import json
import os

import jsonschema

from ansible.module_utils.basic import AnsibleModule

# Dual-path imports: prefer the collection-installed FQCN so the module works
# naturally under ``ansible-galaxy collection install devitops.ansible``;
# fall back to a sys.path hack so the subprocess integration tests and direct
# ``python plugins/modules/read_config.py`` invocations still work from a
# fresh checkout.
try:
    from ansible_collections.devitops.ansible.plugins.module_utils.read_config_core.base import (  # noqa: E501
        MergeEngine,
    )
    from ansible_collections.devitops.ansible.plugins.module_utils.read_config_core.cache import (  # noqa: E501, F401
        ConfigCache,
    )
    from ansible_collections.devitops.ansible.plugins.module_utils.read_config_core.filesystem import (  # noqa: E501, F401
        FilesystemBackend,
        find_directories_with_role_config,
        get_config_file_if_exists,
        validate_path_security,
    )
    from ansible_collections.devitops.ansible.plugins.module_utils.read_config_core.registry import (  # noqa: E501
        available_backends,
        get_backend,
    )
except ImportError:
    import sys

    _HERE = os.path.dirname(os.path.abspath(__file__))
    _MODULE_UTILS = os.path.normpath(os.path.join(_HERE, "..", "module_utils"))
    if _MODULE_UTILS not in sys.path:
        sys.path.insert(0, _MODULE_UTILS)

    from read_config_core.base import MergeEngine
    from read_config_core.cache import ConfigCache  # noqa: F401  (re-export)
    from read_config_core.filesystem import (  # noqa: F401  (re-export helpers)
        FilesystemBackend,
        find_directories_with_role_config,
        get_config_file_if_exists,
        validate_path_security,
    )
    from read_config_core.registry import available_backends, get_backend


def build_merged_config_for_directory(
    target_dir: str,
    config_dir: str,
    role_name: str,
    config_cache: ConfigCache,
    format_type: str = "yaml",
    dry_run: bool = False,
) -> tuple[dict, list[str]]:
    """Back-compat wrapper that delegates to the filesystem backend + engine.

    Preserves the pre-refactor signature and the RuntimeError-on-traversal
    contract that existing callers and tests rely on.
    """
    backend = FilesystemBackend(
        root=config_dir, format_type=format_type, cache=config_cache
    )
    engine = MergeEngine(backend)
    try:
        result = engine.build(target_dir, role_name, dry_run=dry_run)
    except ValueError as exc:
        raise RuntimeError(str(exc))
    return result.data, result.sources


def find_role_vars_dir(role_name: str) -> str | None:
    """Locate a role's vars/ directory via ANSIBLE_CONFIG / ansible.cfg."""
    config_path = os.getenv("ANSIBLE_CONFIG")

    if not config_path:
        for env_var, filename in [("ANSIBLE_HOME", "ansible.cfg"), ("HOME", "ansible.cfg")]:
            base_path = os.getenv(env_var)
            if base_path:
                path = os.path.join(base_path, filename)
                if os.path.isfile(path):
                    config_path = path
                    break

    if not config_path or not os.path.isfile(config_path):
        return None

    config = configparser.ConfigParser()
    try:
        config.read(config_path)
        roles_paths = config.get("defaults", "roles_path", fallback="")
        for base_path in roles_paths.split(":"):
            potential_path = os.path.join(base_path, role_name, "vars")
            potential_path = potential_path.replace("~", os.getenv("HOME") or "~")
            if os.path.exists(potential_path):
                return potential_path
    except Exception:
        return None

    return None


def validate_against_schema(data: dict, schema_path: str) -> bool:
    """Validate ``data`` against the JSON schema at ``schema_path``."""
    try:
        with open(schema_path, "r") as f:
            schema = json.load(f)
        jsonschema.validate(instance=data, schema=schema)
        return True
    except Exception as exc:
        raise ValueError(f"Schema validation failed: {exc}")


def _build_filesystem_backend(
    config_dir: str, format_type: str, track_changes: bool, role_name: str
) -> tuple[FilesystemBackend, ConfigCache, str | None]:
    cache = ConfigCache()
    checksum_file: str | None = None
    if track_changes:
        checksum_file = os.path.join(config_dir, f".{role_name}_checksums.json")
        cache.load_previous_checksums(checksum_file)
    backend = get_backend(
        "filesystem", root=config_dir, format_type=format_type, cache=cache
    )
    return backend, cache, checksum_file


def run_module() -> None:
    """Ansible entrypoint: parse params, build a backend + engine, emit facts."""
    module_args = dict(
        role_name=dict(type="str", required=True, no_log=False),
        config_dir=dict(type="path", required=False, default=None),
        config_path=dict(type="str", required=False, default=None),
        config_tag=dict(type="str", required=False, default=None),
        dry_run=dict(type="bool", required=False, default=False),
        validate_schema=dict(type="str", required=False, default=None),
        format=dict(
            type="str", required=False, default="yaml", choices=["yaml", "json", "ini"]
        ),
        track_changes=dict(type="bool", required=False, default=False),
        backend=dict(
            type="str",
            required=False,
            default="filesystem",
            choices=available_backends(),
        ),
        backend_options=dict(type="dict", required=False, default=None),
    )

    result: dict = dict(changed=False, ansible_facts={})
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    try:
        role_name = module.params["role_name"]
        config_dir = module.params["config_dir"]
        config_path = module.params["config_path"]
        config_tag = module.params["config_tag"]
        dry_run = module.params["dry_run"]
        validate_schema = module.params["validate_schema"]
        format_type = module.params["format"]
        track_changes = module.params["track_changes"]
        backend_name = module.params["backend"]
        backend_options = module.params["backend_options"] or {}

        # Role-name validation (backend-agnostic).
        if not role_name or not role_name.strip():
            module.fail_json(msg="role_name cannot be empty")
        if os.sep in role_name or "/" in role_name or "\\" in role_name:
            module.fail_json(msg="role_name cannot contain path separators")

        # track_changes is currently filesystem-only; other backends need a
        # generalized fingerprint store (a future phase).
        if track_changes and backend_name != "filesystem":
            module.fail_json(
                msg=(
                    f"track_changes is only supported for the filesystem backend "
                    f"(got backend={backend_name!r})"
                )
            )

        # Filesystem-specific input validation + backend instantiation.
        if backend_name == "filesystem":
            if not config_dir:
                config_dir = find_role_vars_dir(role_name)
                if not config_dir:
                    module.fail_json(
                        msg=f"Could not determine config_dir for role: {role_name}"
                    )
            if not os.path.exists(config_dir):
                module.fail_json(
                    msg=f"Configuration directory does not exist: {config_dir}"
                )
            if not os.access(config_dir, os.R_OK):
                module.fail_json(
                    msg=f"Configuration directory is not readable: {config_dir}"
                )
            backend, cache, checksum_file = _build_filesystem_backend(
                config_dir, format_type, track_changes, role_name
            )
        else:
            try:
                backend = get_backend(backend_name, **backend_options)
            except (TypeError, ValueError) as exc:
                module.fail_json(
                    msg=f"Failed to instantiate backend {backend_name!r}: {exc}"
                )
            cache = None
            checksum_file = None

        engine = MergeEngine(backend)

        discovered = list(engine.discover(role_name))

        if not discovered:
            result["ansible_facts"] = {
                "read_config": {
                    "mode": "single" if config_path else "multiple",
                    "configs": {},
                    "matched_count": 0,
                }
            }
            module.exit_json(**result)

        if config_path:
            try:
                merge_result = engine.build(config_path, role_name, dry_run=dry_run)
            except ValueError as exc:
                module.fail_json(msg=str(exc))

            if validate_schema and not dry_run:
                try:
                    validate_against_schema(merge_result.data, validate_schema)
                except ValueError as exc:
                    module.fail_json(msg=str(exc))

            if config_tag and merge_result.data.get("config_tag") != config_tag:
                configs: dict = {}
            else:
                configs = {
                    config_path: {
                        "meta": {"files_merged": merge_result.sources},
                        "data": merge_result.data,
                    }
                }
            result["ansible_facts"] = {
                "read_config": {
                    "mode": "single",
                    "configs": configs,
                    "matched_count": len(configs),
                }
            }

        else:
            merged_configs: dict = {}
            for location in discovered:
                try:
                    merge_result = engine.build(location, role_name, dry_run=dry_run)
                except ValueError as exc:
                    module.fail_json(msg=str(exc))

                if validate_schema and not dry_run:
                    try:
                        validate_against_schema(merge_result.data, validate_schema)
                    except ValueError as exc:
                        module.fail_json(msg=str(exc))

                if config_tag and merge_result.data.get("config_tag") != config_tag:
                    continue
                merged_configs[location] = {
                    "meta": {"files_merged": merge_result.sources},
                    "data": merge_result.data,
                }
            result["ansible_facts"] = {
                "read_config": {
                    "mode": "multiple",
                    "configs": merged_configs,
                    "matched_count": len(merged_configs),
                }
            }

        if track_changes and not dry_run and backend_name == "filesystem" and cache is not None:
            changed_files = cache.get_changed_files()
            if changed_files:
                result["changed"] = True
                result["ansible_facts"]["read_config"]["changed_files"] = list(changed_files)
                assert checksum_file is not None
                cache.save_checksums(checksum_file)

        module.exit_json(**result)

    except Exception as exc:
        module.fail_json(msg=f"Unexpected error: {exc}")


def main() -> None:
    run_module()


if __name__ == "__main__":
    main()
