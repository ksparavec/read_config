# read_config Ansible Module

The `read_config` module is a custom Ansible module that recursively reads and merges YAML configuration files named after a specified role. It supports merging configuration files from a directory structure, filtering by a specific tag, and generating configurations for specific paths.

## Features

- **Recursive Merging**: Reads configuration files named `<role_name>.yaml` or `<role_name>.yml` from a directory structure, merging all relevant files along the path from a base directory to a target directory.
- **Per-Directory Configurations**: When no specific path is provided, the module generates one merged configuration per directory containing a matching file.
- **Single Config Mode**: Restrict output to a single directory by specifying a `config_path`.
- **Tag Filtering**: Optionally filter configurations to include only those containing a specified `config_tag`.
- **Meta Information**: Provides detailed metadata, including a list of configuration files merged for each result.

## Installation

To use the `read_config` module:

1. Create a `library/` folder in your Ansible playbook or role directory.
2. Place the `read_config.py` file inside the `library/` folder.

Ansible will automatically detect modules in the `library/` folder.

## Parameters

| Parameter       | Required | Default | Description                                                                                                                                              |
|------------------|:--------:|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| **role_name**    | **Yes**  | N/A     | Name of the role to search for in `<role_name>.yaml` or `<role_name>.yml` files.                                                                          |
| **config_dir**   | **Yes**  | N/A     | Top-level directory to scan for configuration files.                                                                                                     |
| **config_path**  | No       | `null`  | If specified, restricts output to a single directory. Can be an absolute or relative path to the directory.                                              |
| **config_tag**   | No       | `null`  | If specified, only configurations whose merged data contains `config_tag: <value>` are included in the results.                                           |

## Return Values

The module provides the following return values based on input parameters:

### 1. When `config_path` is **not specified**:

Returns `merged_configs`, a dictionary where each key is the absolute path to a directory containing a configuration file, and the value is the merged configuration data.

Example:
```yaml
merged_configs:
  "/path/to/config":
    meta:
      files_merged:
        - "/path/to/config/testrole.yaml"
    data:
      key1: "value1"
      config_tag: "production"
  "/path/to/config/subfolder":
    meta:
      files_merged:
        - "/path/to/config/testrole.yaml"
        - "/path/to/config/subfolder/testrole.yaml"
    data:
      key2: "value2"
      config_tag: "production"
```

### 2. When `config_path` is specified:

Returns `merged_config`, a dictionary representing the single merged configuration for the specified directory.

Example:
```yaml
merged_config:
  meta:
    files_merged:
      - "/path/to/config/testrole.yaml"
      - "/path/to/config/subfolder/testrole.yaml"
  data:
    key2: "value2"
    config_tag: "production"
```

### 3. Tag Filtering (`config_tag`):

If `config_tag` is specified, only configurations with a matching tag in their final merged data are returned. Non-matching configurations are excluded.


## Usage Examples

### Example Directory Structure

```
/path/to/config/
├── testrole.yaml
├── subfolder1/
│   └── testrole.yaml
└── subfolder2/
    ├── testrole.yaml
    └── subfolder3/
        └── testrole.yaml
```

File contents:

* `/path/to/config/testrole.yaml`:

```yaml
key1: "root_value"
config_tag: "dev"
```

* `/path/to/config/subfolder1/testrole.yaml`:

```yaml
key1: "subfolder1_value"
key2: "value2"
```

* `/path/to/config/subfolder2/testrole.yaml`:

```yaml
key2: "subfolder2_value"
config_tag: "production"
```

* `/path/to/config/subfolder3/testrole.yaml`:

```yaml
key3: "subfolder3_value"
```

### Example Playbooks

#### 1. Read All Configurations

```yaml
- name: Read all configurations for testrole
  read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs

- name: Display all configurations
  debug:
    var: all_configs.ansible_facts.merged_configs
```

#### 2. Read a Single Directory’s Configuration

```yaml
- name: Read configuration for subfolder2
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: subfolder2
  register: single_config

- name: Display single configuration
  debug:
    var: single_config.ansible_facts.merged_config
```

#### 3. Filter Configurations by Tag

```yaml
- name: Read only configurations tagged "production"
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_tag: production
  register: production_configs

- name: Display production configurations
  debug:
    var: production_configs.ansible_facts.merged_configs
```

#### 4. Single Directory + Tag Filtering

```yaml
- name: Read configuration for subfolder2 with "production" tag
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    config_path: subfolder2
    config_tag: production
  register: filtered_config

- name: Display filtered configuration
  debug:
    var: filtered_config.ansible_facts.merged_config
```

### Testing

To test the module:

1. Place the `read_config.py` module in a `library/` folder.
2. Create a test playbook, for example `test_read_config_playbook.yml`:

```yaml
- name: Test read_config module
  hosts: localhost
  gather_facts: false
  tasks:
    - name: Read all configs
      read_config:
        role_name: testrole
        config_dir: ./config
      register: all_configs

    - debug:
        var: all_configs.ansible_facts.merged_configs
```

3. Run the playbook:

```bash
ansible-playbook -i localhost, --connection=local test_read_config_playbook.yml
```

4. Verify that the debug output includes the merged configurations.


## License

This project is licensed under MIT license. For more information, see the [license text](LICENSE.md).

