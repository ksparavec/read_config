# read_config

> Ansible module for hierarchical role configuration with pluggable storage
> backends. Ships as the `devitops.ansible` collection.

Start with YAML/JSON/INI files on disk. When the data outgrows the repo, move
the same merge hierarchy into SQL, Redis, etcd, Consul, or a REST API — the
module's merge semantics don't change when you switch backends.

## Motivation

Ansible roles usually need configuration that varies by environment,
datacenter, host, or service — on top of shared defaults. The usual tools
(`group_vars`, `host_vars`, `vars_files`, Jinja conditionals) handle simple
cases, but start to strain when:

- values live in nested dicts that need **deep merging**;
- override precedence follows a **hierarchy** (global → env → datacenter →
  host);
- config data lives **outside inventory** (a database, a config server,
  Consul, a REST API);
- the same merged result is consumed by **multiple roles**.

`read_config` takes a role-identified set of sources (YAML files named
`<role>.yaml`, rows in a SQL table keyed by `role_name`, Redis keys under
`.../<role>/...`, etc.) and merges them in a deterministic order, with child
values overriding parent values via Ansible's `dict_merge`. The storage
backend is pluggable; the merge behavior is not.

## Merge semantics at a glance

Given this tree:

```
config/
├── myrole.yaml                   # k1: base, k2: {a: 1, b: 2}
└── production/
    └── myrole.yaml               # k2: {b: override, c: 3}, k3: prod
```

Calling:

```yaml
- devitops.ansible.read_config:
    role_name: myrole
    config_dir: ./config
    config_path: ./config/production
```

Produces:

```yaml
k1: base             # inherited unchanged from parent
k2:
  a: 1               # inherited from parent
  b: override        # child wins on collision
  c: 3               # added by child
k3: prod             # added by child
```

Rules:

- Dicts are merged **recursively** (deep merge).
- Scalars follow **child-wins** precedence.
- Levels whose role file is absent are silently skipped.
- Every result carries a `files_merged` list for provenance.

The same conceptual model applies to every backend: "parent" for SQL/KV
means a shorter `/`-delimited path; for HTTP it means an earlier layer in
the configured request chain.

## Features

- **Pluggable backends** — `filesystem`, `sql`, `redis`, `etcd`, `consul`,
  `http`. Third-party backends implement a six-method Protocol.
  ```yaml
  backend: redis
  backend_options:
    url: redis://redis.example.com:6379/0
    prefix: role_configs
  ```

- **Multiple file formats** (filesystem) — YAML (`.yaml`/`.yml`), JSON,
  INI (`.ini`/`.cfg`).
  ```yaml
  format: json
  ```

- **Hierarchical deep merge** — child values override parent values;
  sub-dicts are merged, not replaced.
  ```text
  # /config/myrole.yaml          -> timeouts: {connect: 5, read: 30}
  # /config/prod/myrole.yaml     -> timeouts: {read: 60}
  # merged output                -> timeouts: {connect: 5, read: 60}
  ```

- **Tag filtering** — return only configs whose merged data includes a
  specific `config_tag`.
  ```yaml
  config_tag: production
  ```

- **Schema validation** — validate every merged result against a JSON
  Schema before returning it.
  ```yaml
  validate_schema: "{{ role_path }}/files/config.schema.json"
  ```

- **Change tracking** (filesystem only) — hash source files, compare
  against the previous run, set `changed: true` when anything drifted.
  ```yaml
  track_changes: true
  ```

- **Dry run** — report which sources *would* contribute to the merge
  without reading them.
  ```yaml
  dry_run: true
  ```

- **Provenance** — every result carries an ordered list of sources that
  contributed to it.
  ```yaml
  ansible_facts:
    read_config:
      configs:
        "/config/production":
          meta:
            files_merged:
              - /config/myrole.yaml
              - /config/production/myrole.yaml
  ```

## Backends at a glance

| Backend      | Hierarchy model                      | Fingerprint               |
|--------------|--------------------------------------|---------------------------|
| `filesystem` | directory tree                        | SHA-256 of file bytes     |
| `sql`        | path-based (`location` column)        | SHA-256 of row JSON       |
| `redis`      | key-prefix                            | SHA-256 of value          |
| `etcd`       | key-prefix                            | `mod_revision`            |
| `consul`     | key-prefix                            | `ModifyIndex`             |
| `http`       | ordered list of GET endpoints         | `ETag` or SHA-256 of body |

## Installation

### From Ansible Galaxy

```bash
ansible-galaxy collection install devitops.ansible
```

Or pin a version via `requirements.yml`:

```yaml
collections:
  - name: devitops.ansible
    version: ">=1.0.0,<2.0.0"
```

```bash
ansible-galaxy collection install -r requirements.yml
```

### From source

```bash
git clone https://github.com/ksparavec/read_config.git
cd read_config
make build          # produces devitops-ansible-1.0.0.tar.gz
make install-local  # installs into ~/.ansible/collections
```

### Python dependencies per backend

Install only what your chosen backend needs:

```bash
pip install pyyaml jsonschema          # filesystem + schema validation (core)
pip install sqlalchemy                 # sql backend
pip install redis                      # redis backend
pip install etcd3                      # etcd backend
pip install python-consul              # consul backend
pip install requests                   # http backend
```

## Parameters

| Parameter           | Required | Default      | Applies to      | Description |
|---------------------|:--------:|--------------|-----------------|-------------|
| **role_name**       | **Yes**  | N/A          | all             | Role identifier used to locate config data. Cannot contain path separators. |
| **backend**         | No       | `filesystem` | all             | Storage backend. One of `filesystem`, `sql`, `redis`, `etcd`, `consul`, `http`. |
| **backend_options** | No       | `null`       | non-filesystem  | Dict of backend-specific kwargs (DSN, URL templates, etc.). See the backend's factory signature. |
| **config_dir**      | No       | role vars dir | filesystem     | Top-level directory to scan. Must exist and be readable. |
| **config_path**     | No       | `null`       | all             | Restrict output to a single location (path for filesystem, key for KV, layer name for HTTP, …). Must resolve inside the backend's root. |
| **config_tag**      | No       | `null`       | all             | Include only configs whose merged data has `config_tag: <value>`. |
| **format**          | No       | `yaml`       | filesystem      | File format: `yaml`, `json`, `ini`. |
| **validate_schema** | No       | `null`       | all             | JSON Schema file to validate merged data against. |
| **track_changes**   | No       | `false`      | filesystem      | Track checksum changes between runs and report `changed: true`. Fails on non-filesystem backends. |
| **dry_run**         | No       | `false`      | all             | Report which sources would be merged without reading them. |

## Backend-specific examples

All examples use the fully-qualified collection name.

### Filesystem (default)

```yaml
- name: Read all YAML configurations
  devitops.ansible.read_config:
    role_name: myrole
    config_dir: /etc/myapp/config
  register: all_configs
```

### SQL (SQLAlchemy DSN)

```yaml
- name: Read from a Postgres role_configs table
  devitops.ansible.read_config:
    role_name: myrole
    backend: sql
    backend_options:
      dsn: "postgresql+psycopg://user:pass@db.example.com/appdb"
      table: role_configs
    config_path: "production/web/frontend"
  register: sql_configs
  delegate_to: localhost
```

### Redis

```yaml
- name: Read from Redis (key prefix configs/myrole/...)
  devitops.ansible.read_config:
    role_name: myrole
    backend: redis
    backend_options:
      url: redis://redis.example.com:6379/0
      prefix: configs
    config_path: "production"
  register: redis_configs
  delegate_to: localhost
```

### HTTP — layered REST API

```yaml
- name: Read merged parameters from a REST API
  devitops.ansible.read_config:
    role_name: myrole
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

## Full playbook example

A typical webapp deployment where role vars are merged from shared defaults,
env-specific overrides, and per-datacenter overrides.

### Layout

```
.
├── site.yml
├── configs/
│   ├── webapp.yaml
│   ├── staging/
│   │   ├── webapp.yaml
│   │   └── us-east/
│   │       └── webapp.yaml
│   └── production/
│       ├── webapp.yaml
│       ├── us-east/
│       │   └── webapp.yaml
│       └── eu-west/
│           └── webapp.yaml
└── roles/
    └── webapp/
        ├── tasks/main.yml
        ├── templates/app.conf.j2
        ├── handlers/main.yml
        └── files/webapp.schema.json
```

### Config files

```yaml
# configs/webapp.yaml  (shared defaults)
listen_port: 8080
workers: 2
log_level: info
database:
  pool_size: 10
```

```yaml
# configs/production/webapp.yaml
workers: 8
log_level: warn
database:
  pool_size: 50
```

```yaml
# configs/production/eu-west/webapp.yaml
database:
  host: db.eu-west.internal
  pool_size: 40
```

### Role

```yaml
# roles/webapp/tasks/main.yml
- name: Load merged webapp config for this host
  devitops.ansible.read_config:
    role_name: webapp
    config_dir: "{{ playbook_dir }}/configs"
    config_path: "{{ playbook_dir }}/configs/{{ env }}/{{ dc }}"
    validate_schema: "{{ role_path }}/files/webapp.schema.json"
    track_changes: true
  register: cfg
  delegate_to: localhost
  run_once: true

- name: Render webapp.conf
  template:
    src: app.conf.j2
    dest: /etc/webapp/webapp.conf
    mode: "0640"
  vars:
    webapp: "{{ (cfg.ansible_facts.read_config.configs.values() | first).data }}"
  notify: restart webapp
```

```yaml
# roles/webapp/handlers/main.yml
- name: restart webapp
  ansible.builtin.service:
    name: webapp
    state: restarted
```

### Playbook

```yaml
# site.yml
- hosts: webapp_servers
  gather_facts: false
  vars:
    env: production
    dc: eu-west
  roles:
    - webapp
```

### Resulting merge

For a host in `production/eu-west`, the `read_config` task loads:

```yaml
listen_port: 8080            # shared default
workers: 8                   # production override
log_level: warn              # production override
database:
  pool_size: 40              # eu-west override of production's 50
  host: db.eu-west.internal  # eu-west addition
```

Because `track_changes: true` is set, editing any of the three
`webapp.yaml` files on disk causes the next run to report
`changed: true` and fire the `restart webapp` handler. If `.webapp.conf`
renders identically, Ansible's template task still reports `ok` — the
change signal is specifically about the config sources, not the rendered
output.

## Return values

```yaml
ansible_facts:
  read_config:
    mode: multiple          # or 'single' when config_path is specified
    configs:
      "/absolute/path/to/location":
        meta:
          files_merged:
            - "/absolute/path/to/myrole.yaml"
            - "/absolute/path/to/location/myrole.yaml"
        data:
          key1: value1
          config_tag: production
    matched_count: 1
    changed_files:           # only when track_changes is true
      - "/absolute/path/to/location/myrole.yaml"
changed: true                # only when track_changes is true and anything drifted
```

- `mode`: `"single"` when `config_path` was given, `"multiple"` otherwise.
- `configs`: keyed by location identifier; each entry has a `meta.files_merged`
  provenance list and a `data` dict with the merged payload.
- `matched_count`: number of entries in `configs` (after `config_tag` filtering).
- `changed_files` / `changed`: present only when `track_changes: true`.

## Security

- Filesystem backend: every resolved path must lie inside `config_dir`.
  Path-traversal attempts (`..` segments, symlinks pointing outside the
  root) raise `ValueError`.
- Role names may not contain path separators (`/`, `\`, `os.sep`).
- SQL backend validates table / column / separator identifiers against a
  strict regex to rule out SQL injection via configuration.
- All network backends (SQL, Redis, etcd, Consul, HTTP) should run via
  `delegate_to: localhost` unless the target host actually needs to reach
  the backend directly.
- `validate_schema` catches structurally-invalid merged configs before they
  are consumed downstream.

## Development and testing

The repo has a pytest-based test suite (326 tests, ~96% coverage) plus a
subprocess-based integration suite that invokes the module as a real
Ansible subprocess.

```bash
make venv            # create .venv and install dev deps
make test            # unit tests only (fast)
make integration     # integration tests only
make test-all        # everything
make coverage        # unit tests with terminal coverage report
make coverage-html   # HTML coverage at htmlcov/index.html
```

Layout:

- `tests/unit/` — per-backend unit tests plus the `BackendContract`
  conformance suite (and its two mixins, `ValidatesTargetsContract` and
  `ContentAwareDiscoveryContract`).
- `tests/integration/` — module-as-subprocess end-to-end tests.

To add a new backend, implement the six-method `ConfigBackend` Protocol in
`plugins/module_utils/read_config_core/`, register it via
`register_backend("yourname", YourBackend)`, and subclass `BackendContract`
(plus the applicable mixins) for free conformance coverage.

## License

MIT. See [LICENSE.md](LICENSE.md).
