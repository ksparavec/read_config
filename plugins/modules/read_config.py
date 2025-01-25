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
version_added: "1.0.0"
options:
  role_name:
    description:
      - The name of the role for which configuration files should be read.
    type: str
    required: true
  config_dir:
    description:
      - The top-level directory to search recursively for YAML configuration files.
    type: str
    required: true
  config_path:
    description:
      - If specified, only return the merged config for that specific directory path (absolute or relative to config_dir).
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
author:
  - "Your Name (@yourGitHubHandle)"
'''

EXAMPLES = r'''
- name: Read all role configs for testrole from /path/to/config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs

- name: Show all configs
  debug:
    var: all_configs.ansible_facts.merged_configs

- name: Read only subfolder2 config
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: subfolder2
  register: single_config

- name: Show single config
  debug:
    var: single_config.ansible_facts.merged_config

- name: Read all configs but only those tagged 'production'
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_tag: production
  register: prod_configs

- name: Show production configs
  debug:
    var: prod_configs.ansible_facts.merged_configs
'''

RETURN = r'''
ansible_facts:
  description:
    - Returns either "merged_configs" (a dict of directories => merged config)
      or "merged_config" (a single config) depending on whether config_path is specified.
  type: dict
  returned: always
  sample:
    merged_configs:
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

    # If config_path is given:
    merged_config:
      meta:
        files_merged:
          - ...
      data:
        ...
'''

import os
import yaml
from ansible.module_utils.basic import AnsibleModule

def deep_merge(dict1, dict2):
    """
    Merge dict2 into dict1 using an explicit loop and stack;
    Returns dict1.
    """
    items_to_process = [(dict1, dict2)]
    
    while items_to_process:
        current_dict1, current_dict2 = items_to_process.pop()
        
        for key, value in current_dict2.items():
            if (
                key in current_dict1
                and isinstance(current_dict1[key], dict)
                and isinstance(value, dict)
            ):
                # Instead of recursing, add the nested dicts to our processing queue
                items_to_process.append((current_dict1[key], value))
            else:
                current_dict1[key] = value
    
    return dict1

def get_config_file_if_exists(directory, role_name):
    """
    If directory has <role_name>.yaml or <role_name>.yml, return its absolute path;
    otherwise return None.
    """
    candidates = [f"{role_name}.yaml", f"{role_name}.yml"]
    for candidate in candidates:
        cfg_path = os.path.join(directory, candidate)
        if os.path.isfile(cfg_path):
            return cfg_path
    return None

def find_directories_with_role_config(config_dir, role_name):
    """
    Walk through config_dir and find each directory that contains a file
    named <role_name>.yaml or <role_name>.yml. Return a set of directories.
    """
    dirs_with_configs = set()
    for root, dirs, files in os.walk(config_dir):
        for f in files:
            if f in [f"{role_name}.yaml", f"{role_name}.yml"]:
                dirs_with_configs.add(root)
    return dirs_with_configs

def build_merged_config_for_directory(target_dir, config_dir, role_name):
    """
    Build a merged config for 'target_dir' by scanning from config_dir -> subfolder -> ... -> target_dir.
    Each subpath is included in the merge if it has a <role_name>.yaml|yml file.
    Return (merged_data, files_merged).
    """
    config_dir_abs = os.path.abspath(config_dir)
    target_dir_abs = os.path.abspath(target_dir)

    # Optional check to ensure target_dir_abs is inside config_dir_abs
    if not target_dir_abs.startswith(config_dir_abs):
        raise RuntimeError(
            f"Target directory {target_dir_abs} is not under config_dir {config_dir_abs}"
        )

    # Build a list of all intermediate paths from config_dir_abs down to target_dir_abs
    # e.g. if config_dir_abs=/foo and target_dir_abs=/foo/bar/baz, then subpaths = [
    #   /foo,
    #   /foo/bar,
    #   /foo/bar/baz
    # ]
    subpaths = []
    rel_parts = os.path.relpath(target_dir_abs, config_dir_abs).split(os.path.sep)
    current_path = config_dir_abs
    subpaths.append(current_path)

    for part in rel_parts:
        current_path = os.path.join(current_path, part)
        subpaths.append(current_path)

    merged_data = {}
    files_merged = []

    # Merge each subpath if it has a config file
    for subpath in subpaths:
        cfg_file = get_config_file_if_exists(subpath, role_name)
        if cfg_file:
            try:
                with open(cfg_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f) or {}
                deep_merge(merged_data, data)
                files_merged.append(cfg_file)
            except Exception as e:
                raise RuntimeError(f"Error reading configuration file {cfg_file}: {e}")

    return (merged_data, files_merged)

def run_module():
    module_args = dict(
        role_name=dict(type='str', required=True),
        config_dir=dict(type='str', required=True),
        config_path=dict(type='str', required=False, default=None),
        config_tag=dict(type='str', required=False, default=None)
    )

    result = dict(changed=False, ansible_facts={})

    module = AnsibleModule(argument_spec=module_args, supports_check_mode=True)
    role_name = module.params['role_name']
    config_dir = module.params['config_dir']
    config_path = module.params['config_path']
    config_tag = module.params['config_tag']

    # Convert config_dir to absolute so any relative path is normalized
    config_dir = os.path.abspath(config_dir)
    if not os.path.exists(config_dir):
        module.fail_json(msg=f"Configuration directory does not exist: {config_dir}")

    # Find directories that directly contain <role_name>.yaml or .yml
    dirs_with_configs = find_directories_with_role_config(config_dir, role_name)

    if not dirs_with_configs:
        # No matching files found anywhere
        if config_path:
            # We intended to get a single config, but there's none at all
            result['ansible_facts']['merged_config'] = {}
        else:
            # Return empty dictionary of configs
            result['ansible_facts']['merged_configs'] = {}
        module.exit_json(**result)

    # If config_path is specified => return a single config
    if config_path:
        # Convert config_path to absolute if it's not already
        if not os.path.isabs(config_path):
            config_path = os.path.join(config_dir, config_path)
        config_path_abs = os.path.abspath(config_path)

        # We do the build even if subfolder doesn't contain a direct file,
        # because it may inherit from parent directories.
        merged_data, files_merged = build_merged_config_for_directory(
            target_dir=config_path_abs,
            config_dir=config_dir,
            role_name=role_name
        )

        # Now if config_tag is given, we only keep this config if it matches
        if config_tag:
            # If the final config doesn't have config_tag or doesn't match, return empty
            if merged_data.get('config_tag') != config_tag:
                result['ansible_facts']['merged_config'] = {}
                module.exit_json(**result)

        # If we get here => either no config_tag was specified or it matches
        result['ansible_facts']['merged_config'] = {
            'meta': {
                'files_merged': files_merged
            },
            'data': merged_data
        }
        module.exit_json(**result)

    else:
        # No config_path => return multiple configs in merged_configs
        merged_configs = {}
        for d in dirs_with_configs:
            merged_data, files_merged = build_merged_config_for_directory(
                target_dir=d,
                config_dir=config_dir,
                role_name=role_name
            )
            # Check tag if specified
            if config_tag:
                if merged_data.get('config_tag') != config_tag:
                    # Skip this directory if it doesn't match
                    continue

            merged_configs[d] = {
                'meta': {
                    'files_merged': files_merged
                },
                'data': merged_data
            }

        result['ansible_facts']['merged_configs'] = merged_configs
        module.exit_json(**result)

def main():
    run_module()

if __name__ == '__main__':
    main()
