# read_config Ansible Module

The `read_config` module is a custom Ansible module that recursively reads and merges configuration files named after a specified role. It supports multiple formats (YAML, JSON, INI), merging configuration files from a directory structure, filtering by a specific tag, and generating configurations for specific paths.

## Features

- **Multiple Format Support**: Reads configuration files in YAML, JSON, or INI format.
- **Recursive Merging**: Reads configuration files named `<role_name>.<format>` from a directory structure, merging all relevant files along the path from a base directory to a target directory.
- **Per-Directory Configurations**: When no specific path is provided, the module generates one merged configuration per directory containing a matching file.
- **Single Config Mode**: Restrict output to a single directory by specifying a `config_path`.
- **Tag Filtering**: Optionally filter configurations to include only those containing a specified `config_tag`.
- **Schema Validation**: Optional JSON schema validation for configuration files.
- **Change Tracking**: Track configuration changes between runs.
- **Dry Run Mode**: Preview which files would be merged without reading them.
- **Meta Information**: Provides detailed metadata, including a list of configuration files merged for each result.

## Installation

To use the `read_config` module:

1. Create a `library/` folder in your Ansible playbook or role directory.
2. Place the `read_config.py` file inside the `library/` folder.
3. Install required Python packages:
   ```bash
   pip install pyyaml jsonschema
   ```

Ansible will automatically detect modules in the `library/` folder.

## Parameters

| Parameter       | Required | Default | Description                                                                                                                                              |
|------------------|:--------:|---------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| **role_name**    | **Yes**  | N/A     | Name of the role to search for in configuration files. Cannot contain path separators.                                                                   |
| **config_dir**   | No       | `<role_path>/<role_name>/vars` | Top-level directory to scan for configuration files. Must exist and be readable.                                                                        |
| **config_path**  | No       | `null`  | If specified, restricts output to a single directory. Must be within config_dir to prevent path traversal.                                               |
| **config_tag**   | No       | `null`  | If specified, only configurations whose merged data contains `config_tag: <value>` are included in the results.                                           |
| **format**       | No       | `yaml`  | Format of the configuration files to read. Choices: `yaml`, `json`, `ini`.                                                                               |
| **validate_schema** | No   | `null`  | Optional JSON schema file path to validate configurations against.                                                                                       |
| **track_changes** | No     | `false` | If true, track configuration changes between runs and report changed files.                                                                              |
| **dry_run**      | No       | `false` | If true, show what files would be merged without reading them.                                                                                           |

## Return Values

The module provides the following return values based on input parameters:

### 1. Standard Return Structure:

```yaml
ansible_facts:
  read_config:
    mode: multiple  # or 'single' when config_path is specified
    configs:
      "/path/to/config":
        meta:
          files_merged:
            - "/path/to/config/testrole.yaml"
        data:
          key1: "value1"
          config_tag: "production"
    matched_count: 1
    changed_files:  # Only present when track_changes is true
      - "/path/to/config/testrole.yaml"
changed: true  # Only present when track_changes is true and changes detected
```

### 2. Format Support:

The module supports multiple configuration formats:

- **YAML**: Files with `.yaml` or `.yml` extensions
- **JSON**: Files with `.json` extension
- **INI**: Files with `.ini` or `.cfg` extensions

### 3. Schema Validation:

When `validate_schema` is specified, the module validates each configuration against the provided JSON schema. If validation fails, the module fails with a descriptive error message.

### 4. Change Tracking:

When `track_changes` is enabled, the module:
- Tracks file changes between runs using checksums
- Reports changed files in the `changed_files` list
- Sets `changed: true` when changes are detected
- Stores checksums in a hidden file in the config directory

## Usage Examples

### 1. Basic Usage with YAML

```yaml
- name: Read all YAML configurations
  read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs
```

### 2. Using JSON Format

```yaml
- name: Read JSON configurations
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    format: json
  register: json_configs
```

### 3. Schema Validation

```yaml
- name: Read and validate configurations
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    validate_schema: /path/to/schema.json
  register: validated_configs
```

### 4. Change Tracking

```yaml
- name: Track configuration changes
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    track_changes: true
  register: tracked_configs

- name: Show changed files
  debug:
    var: tracked_configs.ansible_facts.read_config.changed_files
  when: tracked_configs.changed
```

### 5. Dry Run

```yaml
- name: Preview configuration merge
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    dry_run: true
  register: preview_configs
```

### 6. Combined Features

```yaml
- name: Read and validate JSON configs with change tracking
  read_config:
    role_name: testrole
    config_dir: /path/to/config
    format: json
    validate_schema: /path/to/schema.json
    track_changes: true
    config_tag: production
  register: complex_configs
```

## Testing

The module includes a comprehensive test suite that covers all features and edge cases. The test playbook (`tests/test_read_config_playbook.yml`) provides complete coverage of:

1. **Basic Functionality**:
   - Multiple format support (YAML, JSON, INI)
   - Directory structure handling
   - Configuration merging
   - Tag filtering

2. **Parameter Combinations**:
   - All possible parameter combinations
   - Single vs. multiple configuration modes
   - Path-specific configurations
   - Tag-based filtering

3. **Edge Cases**:
   - Invalid role names
   - Path traversal attempts
   - Non-existent directories
   - Empty configuration files
   - Invalid configuration files
   - Invalid format specifications

4. **Security Testing**:
   - Path traversal prevention
   - Role name validation
   - Directory access validation

5. **Change Tracking**:
   - File change detection
   - Checksum management
   - No-change scenarios

6. **Schema Validation**:
   - Valid schema validation
   - Invalid schema handling
   - Format-specific validation

### Running Tests

To run the test suite:

1. Place the `read_config.py` module in a `library/` folder
2. Run the test playbook:

```bash
ansible-playbook -i localhost, --connection=local tests/test_read_config_playbook.yml
```

### Test Examples

#### 1. Basic Format Testing

```yaml
- name: Test YAML format
  read_config:
    role_name: testrole
    config_dir: config
    format: yaml
  register: yaml_configs

- name: Verify YAML config structure
  assert:
    that:
      - yaml_configs.ansible_facts.read_config.mode == 'multiple'
      - yaml_configs.ansible_facts.read_config.matched_count > 0
      - yaml_configs.ansible_facts.read_config.configs is defined
```

#### 2. Parameter Combination Testing

```yaml
- name: Test combined features
  read_config:
    role_name: testrole
    config_dir: config
    format: yaml
    validate_schema: config/schema.json
    track_changes: true
    config_tag: production
    config_path: subfolder2
  register: combined_configs
```

#### 3. Error Case Testing

```yaml
- name: Test path traversal attempt
  read_config:
    role_name: testrole
    config_dir: config
    config_path: "../../etc"
  register: path_traversal_result
  failed_when: false

- name: Verify path traversal handling
  assert:
    that:
      - path_traversal_result.failed is true
      - "Path traversal detected" in path_traversal_result.msg
```

#### 4. Change Tracking Testing

```yaml
- name: Test change tracking
  read_config:
    role_name: testrole
    config_dir: config
    track_changes: true
  register: tracked_configs

- name: Verify change tracking
  assert:
    that:
      - tracked_configs.ansible_facts.read_config.mode == 'multiple'
      - tracked_configs.ansible_facts.read_config.matched_count > 0
      - tracked_configs.ansible_facts.read_config.changed_files is defined
```

### Test Environment

The test suite creates a complete test environment with:

1. **Directory Structure**:
   ```
   config/
   ├── subfolder1/
   ├── subfolder2/
   │   └── subfolder3/
   └── invalid_path/
   ```

2. **Test Files**:
   - Configuration files in multiple formats (YAML, JSON, INI)
   - Valid and invalid schema files
   - Empty and invalid configuration files
   - Files with different config tags

3. **Cleanup**:
   - Automatic cleanup of all test files and directories
   - Removal of checksum files

The test suite ensures that the module handles all scenarios correctly and maintains security and reliability across all features.

## Security Considerations

- The module prevents path traversal attacks by validating all paths against the base config directory
- Role names cannot contain path separators
- Configuration directories must exist and be readable
- Schema validation helps ensure configuration integrity

## License

This project is licensed under MIT license. For more information, see the [license text](LICENSE.md).

