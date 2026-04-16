# devitops.read_config — Ansible Collection

The `devitops.read_config` collection ships the `read_config` module: an
Ansible module that recursively reads and merges hierarchical role
configurations from **pluggable storage backends**. Start with YAML/JSON/INI
files on disk, or fetch the same logical config hierarchy from an SQL
database, Redis, etcd, Consul, or any REST API — the merge semantics are
identical regardless of source.

## Features

- **Pluggable backends**: `filesystem`, `sql`, `redis`, `etcd`, `consul`, `http`.
  Add your own by implementing a six-method Protocol and calling
  `register_backend(name, factory)`.
- **Multiple file formats** (filesystem backend): YAML, JSON, INI.
- **Hierarchical merging**: along directory paths for files, along
  configurable keys/rows/prefixes/URLs for other backends.
- **Tag filtering**: include only configs whose merged data has
  `config_tag: <value>`.
- **Schema validation**: validate merged data against a JSON Schema.
- **Change tracking** (filesystem-only): detect when source files change
  between runs and report `changed: true`.
- **Dry run**: preview which sources would be merged without reading them.
- **Provenance**: every result includes the ordered list of sources that
  contributed to the merge.

## Installation

Build and install locally from a checkout:

```bash
git clone https://github.com/ksparavec/read_config.git
cd read_config
make build          # produces devitops-read_config-1.0.0.tar.gz
make install-local  # installs into ~/.ansible/collections
```

Then use via its fully-qualified collection name:

```yaml
- name: Read merged role config
  devitops.read_config.read_config:
    role_name: myrole
    config_dir: /etc/myapp/config
  register: result
```

Required Python packages (pick only the backends you use):

```bash
pip install pyyaml jsonschema          # filesystem + schema validation
pip install sqlalchemy                 # sql backend
pip install redis                      # redis backend
pip install etcd3                      # etcd backend
pip install python-consul              # consul backend
pip install requests                   # http backend
```

## Backends at a glance

| Backend      | Hierarchy model                     | Fingerprint               |
|--------------|-------------------------------------|---------------------------|
| `filesystem` | directory tree                       | SHA-256 of file bytes     |
| `sql`        | path-based (`location` column)       | SHA-256 of row JSON       |
| `redis`      | key-prefix                           | SHA-256 of value          |
| `etcd`       | key-prefix                           | `mod_revision`            |
| `consul`     | key-prefix                           | `ModifyIndex`             |
| `http`       | ordered list of GET endpoints        | `ETag` or SHA-256 of body |

## Parameters

| Parameter           | Required | Default      | Applies to      | Description |
|---------------------|:--------:|--------------|-----------------|-------------|
| **role_name**       | **Yes**  | N/A          | all             | Role identifier used to locate its config data. Cannot contain path separators. |
| **backend**         | No       | `filesystem` | all             | Storage backend. One of `filesystem`, `sql`, `redis`, `etcd`, `consul`, `http`. |
| **backend_options** | No       | `null`       | non-filesystem  | Dict of backend-specific kwargs (DSN, URL templates, etc.). See the backend's factory signature. |
| **config_dir**      | No       | role vars dir | filesystem      | Top-level directory to scan. Must exist and be readable. |
| **config_path**     | No       | `null`       | all             | Restrict output to a single location (path for filesystem, key for KV, layer name for HTTP, etc.). Must resolve inside the backend's root. |
| **config_tag**      | No       | `null`       | all             | Include only configs whose merged data has `config_tag: <value>`. |
| **format**          | No       | `yaml`       | filesystem      | File format: `yaml`, `json`, `ini`. |
| **validate_schema** | No       | `null`       | all             | JSON Schema file to validate merged data against. |
| **track_changes**   | No       | `false`      | filesystem      | Track checksum changes between runs and report `changed: true`. Fails on non-filesystem backends. |
| **dry_run**         | No       | `false`      | all             | Report which sources would be merged without reading them. |

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

All examples use the fully-qualified collection name.

### 1. Filesystem backend (default)

```yaml
- name: Read all YAML configurations
  devitops.read_config.read_config:
    role_name: testrole
    config_dir: /path/to/config
  register: all_configs
```

### 2. SQL backend (SQLAlchemy DSN)

```yaml
- name: Read from a Postgres role_configs table
  devitops.read_config.read_config:
    role_name: testrole
    backend: sql
    backend_options:
      dsn: "postgresql+psycopg://user:pass@db.example.com/appdb"
      table: role_configs
    config_path: "production/web/frontend"
  register: sql_configs
  delegate_to: localhost
```

### 3. Redis backend

```yaml
- name: Read from Redis (key prefix configs/testrole/...)
  devitops.read_config.read_config:
    role_name: testrole
    backend: redis
    backend_options:
      url: redis://redis.example.com:6379/0
      prefix: configs
    config_path: "production"
  register: redis_configs
  delegate_to: localhost
```

### 4. HTTP backend (layered REST API)

```yaml
- name: Read merged parameters from a REST API
  devitops.read_config.read_config:
    role_name: testrole
    backend: http
    backend_options:
      auth_token: "{{ api_token }}"
      timeout: 10
      context:
        organization_id: 3
        host_id: 42
      layers:
        - name: organization
          url: "https://api.example.com/v1/organizations/{organization_id}/parameters"
          params: {per_page: "all"}
          data_path: "results"
          list_name_key: "name"
          required_context: [organization_id]
        - name: host
          url: "https://api.example.com/v1/hosts/{host_id}/parameters"
          params: {per_page: "all"}
          data_path: "results"
          list_name_key: "name"
          required_context: [host_id]
    config_path: host
  register: http_configs
  delegate_to: localhost
```

### 5. Schema validation (any backend)

```yaml
- name: Read and validate
  devitops.read_config.read_config:
    role_name: testrole
    config_dir: /path/to/config
    validate_schema: /path/to/schema.json
  register: validated_configs
```

### 6. Change tracking (filesystem only)

```yaml
- name: Track configuration changes
  devitops.read_config.read_config:
    role_name: testrole
    config_dir: /path/to/config
    track_changes: true
  register: tracked_configs
  notify: restart service
```

### 7. Dry run

```yaml
- name: Preview the merge without reading files
  devitops.read_config.read_config:
    role_name: testrole
    config_dir: /path/to/config
    dry_run: true
  register: preview_configs
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

