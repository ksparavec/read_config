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
  - Additional backends can be added by calling
    C(read_config_core.registry.register_backend) before this module builds its
    argument spec; the C(backend) choices are computed from the registry at that
    point, so a backend registered later cannot be selected.
  - If no config_path is specified, multiple configs are returned in
    C(ansible_facts.read_config.configs). If config_path is specified, only that
    location's merged config is returned.
  - An optional parameter C(config_tag) filters out configs whose final merged
    data does not include a matching C(config_tag) key/value.
version_added: "1.0.0"
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
        configuration files. Must exist and be readable.
      - If omitted, the module looks for an ansible.cfg via C(ANSIBLE_CONFIG),
        then C($ANSIBLE_HOME/ansible.cfg), then C($HOME/ansible.cfg), reads
        C(defaults.roles_path) from it, and uses the first
        C(<roles_path>/<role_name>/vars) directory that exists. This is a
        narrower search than ansible-core's own config precedence; passing
        config_dir explicitly is more predictable.
    type: path
    required: false
  config_path:
    description:
      - If specified, only return the merged config for that specific location.
      - For the filesystem backend this must be an ABSOLUTE path inside
        config_dir. A relative value is resolved against the working directory
        of the Ansible process, not against config_dir, and will normally fail
        as a path-traversal attempt.
      - For SQL and key-value backends this is a separator-delimited location.
      - For the API backend this is a layer name.
    type: str
    required: false
    default: null
  config_tag:
    description:
      - If specified, only return configs whose final merged data includes a
        C(config_tag) key equal to this value.
    type: str
    required: false
    default: null
  dry_run:
    description:
      - If true, report which sources would contribute to the merge without
        merging them. Every returned config has an empty C(data) dict and a
        populated C(meta.files_merged) list.
      - Schema validation and change tracking are both skipped.
      - Non-filesystem backends still query the store to test existence, so a
        dry run is not free against a remote backend.
    type: bool
    required: false
    default: false
  validate_schema:
    description:
      - Optional JSON schema file path to validate merged configurations against.
      - Applied to every merged config BEFORE config_tag filtering, so a config
        that would have been filtered out can still fail the task.
      - Skipped entirely when dry_run is true.
      - The path must be a regular file.
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
      - Filesystem backend only; fails the task on any other backend.
      - Writes a checksum file named C(.<role_name>_checksums.json) INTO
        config_dir, which must therefore be writable.
      - The first run always reports changed, because no previous checksum file
        exists to compare against.
      - Not honored in check mode - the checksum file is written even under
        --check.
    type: bool
    required: false
    default: false
  backend:
    description:
      - Storage backend to use. Built-in backends are C(filesystem), C(sql),
        C(redis), C(etcd), C(consul), and C(api). Additional backends may be
        registered at runtime via C(read_config_core.registry.register_backend).
    type: str
    required: false
    default: filesystem
    choices: [filesystem, sql, redis, etcd, consul, api]
  backend_options:
    description:
      - Structural, non-secret options passed as keyword arguments to the
        backend factory. Ignored by the filesystem backend; use C(config_dir)
        and C(format) instead.
      - C(sql) - table, role_column, location_column, data_column, separator.
      - C(redis) - prefix, separator, plus any redis.from_url kwargs.
      - C(etcd) - host, port, prefix, separator, plus any etcd3.client kwargs.
        Note host/port, NOT url.
      - C(consul) - host, port, prefix, separator, plus any consul.Consul
        kwargs. Note host/port, NOT url.
      - C(api) - layers and/or api (a preset name) plus base_url,
        preset_options, excludes, headers, auth_header, auth_scheme, timeout,
        verify_tls, allowed_hosts.
      - For C(api), any OTHER key is treated as a template value and
        substituted into the layer URLs, query parameters, and headers, so
        entity ids are passed directly, e.g. an option named C(host_id).
      - Every configured layer is fetched by default; name the ones you do not
        want in C(excludes). A layer whose template needs a value you did not
        supply is an error, and so is supplying a value no layer references -
        which catches a misspelled option name.
      - Put credentials in C(backend_secrets), never here. This option is
        deliberately not C(no_log) so that structural values cannot corrupt
        the returned configuration.
    type: dict
    required: false
    default: null
  backend_secrets:
    description:
      - Credentials for the backend, merged over C(backend_options) before the
        backend factory is called. Marked C(no_log).
      - C(sql) - dsn.
      - C(redis) - url (which carries the password).
      - C(api) - auth_token, or auth as a [user, password] pair.
      - C(etcd) / C(consul) - any credential kwargs the client accepts
        (e.g. C(token) for Consul).
      - A key may not appear in both C(backend_options) and C(backend_secrets).
    type: dict
    required: false
    default: null
author:
  - "Kresimir Sparavec (@ksparavec)"
'''

EXAMPLES = r'''
- name: Read all role configs for testrole from /path/to/config
  devitops.ansible.read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs

# config_path must be ABSOLUTE for the filesystem backend; a relative value
# resolves against the process working directory, not config_dir.
- name: Read only the production subtree
  devitops.ansible.read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: /path/to/config/production
  register: single_config

- name: Read all configs but only those tagged 'production'
  devitops.ansible.read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_tag: production
  register: prod_configs

- name: Validate against a JSON schema and report drift
  devitops.ansible.read_config:
    role_name: testrole
    config_dir: /path/to/config
    validate_schema: "{{ role_path }}/files/testrole.schema.json"
    track_changes: true
  register: checked_configs

- name: Read from a SQL table
  devitops.ansible.read_config:
    role_name: testrole
    backend: sql
    backend_options:
      table: role_configs
    backend_secrets:
      dsn: "postgresql+psycopg://user:pass@db.example.com/appdb"
    config_path: "global/production/web"
  register: sql_configs
  delegate_to: localhost

- name: Read from Redis
  devitops.ansible.read_config:
    role_name: testrole
    backend: redis
    backend_options:
      prefix: configs
    backend_secrets:
      url: "redis://:secret@redis.example.com:6379/0"
    config_path: "global/production"
  register: redis_configs
  delegate_to: localhost

- name: Read from etcd (host/port, not url)
  devitops.ansible.read_config:
    role_name: testrole
    backend: etcd
    backend_options:
      host: etcd.example.com
      port: 2379
      prefix: configs
    config_path: "global/production"
  register: etcd_configs
  delegate_to: localhost

- name: Read from Consul KV (host/port, not url)
  devitops.ansible.read_config:
    role_name: testrole
    backend: consul
    backend_options:
      host: consul.example.com
      port: 8500
      prefix: configs
    backend_secrets:
      token: "{{ consul_token }}"
    config_path: "global/production"
  register: consul_configs
  delegate_to: localhost

- name: Read merged parameters from Foreman using the shipped preset
  devitops.ansible.read_config:
    role_name: testrole
    backend: api
    backend_options:
      api: foreman
      base_url: https://foreman.example.com
      allowed_hosts: ["foreman.example.com"]
      # The adapter reads this host once and builds the chain from the
      # organization, location, domain, subnet and hostgroup it belongs to.
      host: "{{ inventory_hostname }}"
    backend_secrets:
      auth: ["admin", "{{ foreman_password }}"]
    config_path: host
  register: foreman_configs
  delegate_to: localhost

- name: Read merged variables from AWX / Ansible Tower
  devitops.ansible.read_config:
    role_name: testrole
    backend: api
    backend_options:
      api: awx
      base_url: https://awx.example.com
      allowed_hosts: ["awx.example.com"]
      host: "{{ inventory_hostname }}"
    backend_secrets:
      auth_token: "{{ awx_oauth_token }}"
    config_path: host
  register: awx_configs
  delegate_to: localhost

- name: Read the rendered NetBox config context for a device
  devitops.ansible.read_config:
    role_name: testrole
    backend: api
    backend_options:
      api: netbox
      base_url: https://netbox.example.com
      allowed_hosts: ["netbox.example.com"]
      host: "{{ inventory_hostname }}"
    backend_secrets:
      auth_token: "{{ netbox_token }}"
    config_path: device        # or virtual_machine
  register: netbox_configs
  delegate_to: localhost

# config_path names a LAYER for the api backend. Every configured layer is
# fetched unless named in excludes.
- name: Read merged parameters from a layered REST API
  devitops.ansible.read_config:
    role_name: testrole
    backend: api
    backend_secrets:
      auth_token: "{{ api_token }}"
    backend_options:
      allowed_hosts: ["api.example.com"]
      organization_id: 3
      host_id: 42
      layers:
        - name: organization
          url: "https://api.example.com/v1/organizations/{organization_id}/parameters"
          data_path: results
          list_name_key: name
        - name: host
          url: "https://api.example.com/v1/hosts/{host_id}/parameters"
          data_path: results
          list_name_key: name
    config_path: host
  register: http_configs
  delegate_to: localhost
'''

RETURN = r'''
ansible_facts:
  description:
    - Returns a dict under the C(read_config) key with C(mode), C(configs),
      and C(matched_count).
    - C(mode) is C(single) when config_path was given, C(multiple) otherwise.
    - C(configs) is keyed by location identifier. Each entry has
      C(meta.files_merged) (ordered provenance, lowest precedence first) and
      C(data) (the merged payload; empty when dry_run is true).
    - C(matched_count) is the number of entries in C(configs) after config_tag
      filtering, and is 0 when nothing matched or nothing was discovered.
    - C(changed_files) is present only when track_changes is true AND at least
      one source changed.
    - For the api backend, multi-mode returns exactly one entry, keyed by the
      deepest applicable layer name.
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
      changed_files:
        - "/absolute/path/to/config/testrole.yaml"
changed:
  description:
    - Whether any configuration sources changed since the last run.
    - Only ever true when track_changes is enabled. The first run always
      reports true because there is no previous checksum file.
    - Reported even in check mode, and the checksum file is written anyway.
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
    """Validate ``data`` against the JSON schema at ``schema_path``.

    The schema path is treated as operator-supplied trusted input (typically a
    JSON file shipped inside a role's ``files/`` directory). We refuse to read
    anything that is not a regular file so a symlink-to-pipe or symlink-to-
    device-node cannot cause the module to block or hang.
    """
    try:
        if not os.path.isfile(schema_path):
            raise ValueError(
                f"schema file not found or not a regular file: {schema_path}"
            )
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
        # Structural options only. Deliberately NOT no_log: Ansible implements
        # no_log by substring-scrubbing every string and number it contains
        # from the module's output, which corrupts the returned configuration
        # (a context id of 5 turns every value containing a "5" into a
        # sentinel). Secrets go in backend_secrets instead.
        backend_options=dict(type="dict", required=False, default=None, no_log=False),
        # Credentials only. no_log=True keeps these out of verbose output and
        # callbacks; keeping them separate means the redaction cannot reach
        # structural values or the merged config.
        backend_secrets=dict(type="dict", required=False, default=None, no_log=True),
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
        backend_secrets = module.params["backend_secrets"] or {}
        overlap = set(backend_options) & set(backend_secrets)
        if overlap:
            module.fail_json(
                msg=(
                    "the same key may not appear in both backend_options and "
                    f"backend_secrets: {sorted(overlap)}"
                )
            )
        backend_kwargs = {**backend_options, **backend_secrets}

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
                backend = get_backend(backend_name, **backend_kwargs)
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
