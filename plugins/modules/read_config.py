#!/usr/bin/python
# -*- coding: utf-8 -*-

DOCUMENTATION = r'''
---
module: read_config
short_description: Recursively read and separate YAML configurations for a given role, with optional tagging and path filtering.
description:
  - This module recursively scans a directory for YAML files named "<role_name>.yaml" or "<role_name>.yml".
    It produces one final merged config per directory that directly contains a matching file, merging all parent
    directories that also contain a matching file along the path.
  - If no config_path is specified, multiple configs are returned in C(merged_configs).
    If config_path is specified, only that directory's final merged config is returned in C(merged_config).
  - An optional parameter C(config_tag) can be specified to filter out any configs whose final merged data does
    not have a matching config_tag key/value.
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
      - The top-level directory to search recursively for YAML configuration files.
      - Must exist and be readable.
    type: path
    required: false
  config_path:
    description:
      - If specified, only return the merged config for that specific directory path (absolute or relative to config_dir).
      - Must be within config_dir to prevent path traversal.
    type: str
    required: false
    default: null
  config_tag:
    description:
      - If specified, only return configs whose final merged data includes `config_tag: <value>`.
      - In multi-file merges, if a parent or child folder defines config_tag, the final override matters.
    type: str
    required: false
    default: null
  dry_run:
    description:
      - If true, show what files would be merged without reading them.
    type: bool
    required: false
    default: false
  validate_schema:
    description:
      - Optional JSON schema file path to validate configurations against.
    type: str
    required: false
    default: null
  format:
    description:
      - Format of the configuration files to read.
    type: str
    required: false
    default: yaml
    choices: [yaml, json, ini]
  track_changes:
    description:
      - If true, track configuration changes between runs.
    type: bool
    required: false
    default: false
author:
  - "Kresimir Sparavec (@ksparavec)"
'''

EXAMPLES = r'''
- name: Handle missing configurations gracefully
  read_config:
    role_name: nonexistent_role
    config_dir: /path/to/config
  register: result
  failed_when: false

- name: Use with delegate_to for remote config reading
  read_config:
    role_name: myapp
    config_dir: /etc/myapp/config
  delegate_to: "{{ inventory_hostname }}"

- name: Read all role configs for testrole from /path/to/config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs

- name: Show all configs
  debug:
    var: all_configs.ansible_facts.read_config.configs

- name: Read only subfolder2 config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: subfolder2
  register: single_config

- name: Show single config
  debug:
    var: single_config.ansible_facts.read_config.configs

- name: Read all configs but only those tagged 'production'
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_tag: production
  register: prod_configs

- name: Show production configs
  debug:
    var: prod_configs.ansible_facts.read_config.configs
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
            key2: val2
        "/absolute/path/to/config/subfolder2":
          meta:
            files_merged:
              - "/absolute/path/to/config/testrole.yaml"
              - "/absolute/path/to/config/subfolder2/testrole.yaml"
          data:
            key2:
              subkey2b: val2b
            config_tag: production
      matched_count: 2
      changed_files:
        - "/absolute/path/to/config/testrole.yaml"
        - "/absolute/path/to/config/subfolder2/testrole.yaml"
changed:
  description:
    - Whether any configuration files have changed since the last run.
    - Only set when track_changes is enabled.
  type: bool
  returned: when track_changes is true
  sample: true
'''

import os
import yaml
import json
import configparser
import hashlib
import jsonschema
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.common.dict_transformations import dict_merge

class ConfigCache:
    """Cache for configuration files to improve performance."""
    def __init__(self):
        self._cache = {}
        self._checksums = {}
        self._previous_checksums = {}
        self._changed_files = set()
    
    def load_config(self, filepath, format_type='yaml'):
        """Load and cache a configuration file in the specified format."""
        if filepath not in self._cache:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if format_type == 'yaml':
                        self._cache[filepath] = yaml.safe_load(content) or {}
                    elif format_type == 'json':
                        self._cache[filepath] = json.loads(content) or {}
                    elif format_type == 'ini':
                        config = configparser.ConfigParser()
                        config.read_string(content)
                        self._cache[filepath] = {s: dict(config.items(s)) for s in config.sections()}
                    self._checksums[filepath] = hashlib.sha256(content.encode()).hexdigest()
            except Exception as e:
                raise RuntimeError(f"Error reading {filepath}: {e}")
        return self._cache[filepath].copy()
    
    def load_previous_checksums(self, checksum_file):
        """Load previous checksums from file."""
        try:
            with open(checksum_file, 'r') as f:
                self._previous_checksums = json.load(f)
        except FileNotFoundError:
            self._previous_checksums = {}
    
    def save_checksums(self, checksum_file):
        """Save current checksums to file."""
        with open(checksum_file, 'w') as f:
            json.dump(self._checksums, f)
    
    def get_changed_files(self):
        """Get list of files that have changed since last run."""
        self._changed_files = {
            f for f, checksum in self._checksums.items()
            if f not in self._previous_checksums or self._previous_checksums[f] != checksum
        }
        return self._changed_files

def validate_path_security(base_path, target_path):
    """Ensure target_path is within base_path."""
    base = os.path.abspath(base_path)
    target = os.path.abspath(target_path)
    if not target.startswith(base + os.sep) and target != base:
        raise ValueError(f"Path traversal detected: {target} is outside {base}")
    return target

def get_config_file_if_exists(directory, role_name, format_type='yaml'):
    """If directory has <role_name>.<format>, return its absolute path."""
    extensions = {
        'yaml': ['.yaml', '.yml'],
        'json': ['.json'],
        'ini': ['.ini', '.cfg']
    }
    candidates = [f"{role_name}{ext}" for ext in extensions.get(format_type, ['.yaml', '.yml'])]
    for candidate in candidates:
        cfg_path = os.path.join(directory, candidate)
        if os.path.isfile(cfg_path):
            return cfg_path
    return None

def find_directories_with_role_config(config_dir, role_name, format_type='yaml'):
    """Walk through config_dir and find each directory that contains a matching config file."""
    dirs_with_configs = set()
    extensions = {
        'yaml': ['.yaml', '.yml'],
        'json': ['.json'],
        'ini': ['.ini', '.cfg']
    }
    for root, dirs, files in os.walk(config_dir):
        for f in files:
            if any(f == f"{role_name}{ext}" for ext in extensions.get(format_type, ['.yaml', '.yml'])):
                dirs_with_configs.add(root)
    return dirs_with_configs

def build_merged_config_for_directory(target_dir, config_dir, role_name, config_cache, format_type='yaml', dry_run=False):
    """Build a merged config for 'target_dir' by scanning from config_dir -> subfolder -> ... -> target_dir."""
    config_dir_abs = os.path.abspath(config_dir)
    target_dir_abs = os.path.abspath(target_dir)

    try:
        target_dir_abs = validate_path_security(config_dir_abs, target_dir_abs)
    except ValueError as e:
        raise RuntimeError(str(e))

    subpaths = []
    if config_dir_abs != target_dir_abs:
        rel_parts = os.path.relpath(target_dir_abs, config_dir_abs).split(os.path.sep)
    else:
        rel_parts = []
    current_path = config_dir_abs
    subpaths.append(current_path)

    for part in rel_parts:
        current_path = os.path.join(current_path, part)
        subpaths.append(current_path)

    merged_data = {}
    files_merged = []

    for subpath in subpaths:
        cfg_file = get_config_file_if_exists(subpath, role_name, format_type)
        if cfg_file:
            if dry_run:
                files_merged.append(cfg_file)
            else:
                data = config_cache.load_config(cfg_file, format_type)
                merged_data = dict_merge(merged_data, data)
                files_merged.append(cfg_file)

    return (merged_data, files_merged)

def find_role_vars_dir(role_name):
    """Find the first existent subdirectory "role_name/vars" in the roles_path."""
    config_path = os.getenv('ANSIBLE_CONFIG')
    
    if not config_path:
        for env_var, filename in [('ANSIBLE_HOME', 'ansible.cfg'), ('HOME', 'ansible.cfg')]:
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
        roles_paths = config.get('defaults', 'roles_path', fallback='')
        for base_path in roles_paths.split(':'):
            potential_path = os.path.join(base_path, role_name, 'vars')
            potential_path = potential_path.replace('~', os.getenv('HOME'))
            if os.path.exists(potential_path):
                return potential_path
    except Exception:
        return None
    
    return None

def validate_against_schema(data, schema_path):
    """Validate data against a JSON schema."""
    try:
        with open(schema_path, 'r') as f:
            schema = json.load(f)
        jsonschema.validate(instance=data, schema=schema)
        return True
    except Exception as e:
        raise ValueError(f"Schema validation failed: {str(e)}")

def run_module():
    """Main module execution function."""
    module_args = dict(
        role_name=dict(type='str', required=True, no_log=False),
        config_dir=dict(type='path', required=False, default=None),
        config_path=dict(type='str', required=False, default=None),
        config_tag=dict(type='str', required=False, default=None),
        dry_run=dict(type='bool', required=False, default=False),
        validate_schema=dict(type='str', required=False, default=None),
        format=dict(type='str', required=False, default='yaml', choices=['yaml', 'json', 'ini']),
        track_changes=dict(type='bool', required=False, default=False)
    )

    result = dict(changed=False, ansible_facts={})
    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)

    try:
        role_name = module.params['role_name']
        config_dir = module.params['config_dir']
        config_path = module.params['config_path']
        config_tag = module.params['config_tag']
        dry_run = module.params['dry_run']
        validate_schema = module.params['validate_schema']
        format_type = module.params['format']
        track_changes = module.params['track_changes']

        # Validate role_name
        if not role_name or not role_name.strip():
            module.fail_json(msg="role_name cannot be empty")
        if os.sep in role_name or '/' in role_name or '\\' in role_name:
            module.fail_json(msg="role_name cannot contain path separators")

        # Get config_dir
        if not config_dir:
            config_dir = find_role_vars_dir(role_name)
            if not config_dir:
                module.fail_json(msg=f"Could not determine config_dir for role: {role_name}")

        # Validate config_dir
        if not os.path.exists(config_dir):
            module.fail_json(msg=f"Configuration directory does not exist: {config_dir}")
        if not os.access(config_dir, os.R_OK):
            module.fail_json(msg=f"Configuration directory is not readable: {config_dir}")

        # Initialize config cache
        config_cache = ConfigCache()

        # Handle change tracking
        if track_changes:
            checksum_file = os.path.join(config_dir, f".{role_name}_checksums.json")
            config_cache.load_previous_checksums(checksum_file)

        # Find directories with configs
        dirs_with_configs = find_directories_with_role_config(config_dir, role_name, format_type)

        if not dirs_with_configs:
            result['ansible_facts'] = {
                'read_config': {
                    'mode': 'single' if config_path else 'multiple',
                    'configs': {},
                    'matched_count': 0
                }
            }
            module.exit_json(**result)

        # Handle single config path
        if config_path:
            try:
                merged_data, files_merged = build_merged_config_for_directory(
                    target_dir=config_path,
                    config_dir=config_dir,
                    role_name=role_name,
                    config_cache=config_cache,
                    format_type=format_type,
                    dry_run=dry_run
                )

                # Validate against schema if specified
                if validate_schema and not dry_run:
                    try:
                        validate_against_schema(merged_data, validate_schema)
                    except ValueError as e:
                        module.fail_json(msg=str(e))

                if config_tag and merged_data.get('config_tag') != config_tag:
                    result['ansible_facts'] = {
                        'read_config': {
                            'mode': 'single',
                            'configs': {},
                            'matched_count': 0
                        }
                    }
                else:
                    result['ansible_facts'] = {
                        'read_config': {
                            'mode': 'single',
                            'configs': {
                                config_path: {
                                    'meta': {
                                        'files_merged': files_merged
                                    },
                                    'data': merged_data
                                }
                            },
                            'matched_count': 1
                        }
                    }
            except Exception as e:
                module.fail_json(msg=str(e))

        # Handle multiple configs
        else:
            merged_configs = {}
            for d in dirs_with_configs:
                try:
                    merged_data, files_merged = build_merged_config_for_directory(
                        target_dir=d,
                        config_dir=config_dir,
                        role_name=role_name,
                        config_cache=config_cache,
                        format_type=format_type,
                        dry_run=dry_run
                    )

                    # Validate against schema if specified
                    if validate_schema and not dry_run:
                        try:
                            validate_against_schema(merged_data, validate_schema)
                        except ValueError as e:
                            module.fail_json(msg=str(e))

                    if config_tag and merged_data.get('config_tag') != config_tag:
                        continue

                    merged_configs[d] = {
                        'meta': {
                            'files_merged': files_merged
                        },
                        'data': merged_data
                    }
                except Exception as e:
                    module.fail_json(msg=str(e))

            result['ansible_facts'] = {
                'read_config': {
                    'mode': 'multiple',
                    'configs': merged_configs,
                    'matched_count': len(merged_configs)
                }
            }

        # Handle change tracking
        if track_changes and not dry_run:
            changed_files = config_cache.get_changed_files()
            if changed_files:
                result['changed'] = True
                result['ansible_facts']['read_config']['changed_files'] = list(changed_files)
                config_cache.save_checksums(checksum_file)

        module.exit_json(**result)

    except Exception as e:
        module.fail_json(msg=f"Unexpected error: {str(e)}")

def main():
    run_module()

if __name__ == '__main__':
    main()
