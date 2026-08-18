# read_config reference

Complete reference for the `devitops.ansible.read_config` module. If you are
new to it, start with the [README](README.md) instead — this document assumes
you already know what the module does and are looking for specifics.

## Contents

- [Backends at a glance](#backends-at-a-glance)
- [Merge semantics in detail](#merge-semantics-in-detail)
- [Installing backend dependencies](#installing-backend-dependencies)
- [Parameters](#parameters)
- [backend_options reference](#backend_options-reference)
- [API presets](#api-presets)
- [Backend-specific examples](#backend-specific-examples)
- [API semantics](#api-semantics)
- [Full playbook example](#full-playbook-example)
- [Operational notes](#operational-notes)
- [Return values](#return-values)
- [Security](#security)
- [Development and testing](#development-and-testing)

## Backends at a glance

| Backend      | Hierarchy model                      | Fingerprint               |
|--------------|--------------------------------------|---------------------------|
| `filesystem` | directory tree                        | SHA-256 of file bytes     |
| `sql`        | path-based (`location` column)        | SHA-256 of row JSON       |
| `redis`      | key-prefix                            | SHA-256 of value          |
| `etcd`       | key-prefix                            | `mod_revision`            |
| `consul`     | key-prefix                            | `ModifyIndex`             |
| `api`        | ordered list of GET endpoints         | `ETag` or SHA-256 of body |

## Merge semantics in detail

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

- Lists are **replaced**, not merged or appended. `dict_merge` recurses
  into dicts only; a list at a child level wins outright.

The same conceptual model applies to every backend: "parent" for SQL/KV
means a shorter `/`-delimited path; for HTTP it means an earlier layer in
the configured request chain.

> **One shape difference worth knowing.** A filesystem hierarchy is rooted
> *at* `config_dir`, and that directory is itself a config level. SQL and
> KV chains start at the **first path segment** and have no implicit root:
> `resolve_ancestry("production/web")` is `["production", "production/web"]`.
> A row or key stored at the bare empty location is therefore **never
> merged into its descendants** — it is only returned as its own entry.
> When porting a filesystem tree to SQL/KV, give the root level a name
> (`global`, `all`, `base`) and store it at that segment:
>
> ```text
> filesystem            SQL / KV
> config/myrole.yaml     ->  location "global"
> config/prod/…          ->  location "global/prod"
> ```

## Installing backend dependencies

Install only what your chosen backend needs:

```bash
pip install pyyaml jsonschema          # filesystem + schema validation (core)
pip install sqlalchemy                 # sql backend
pip install redis                      # redis backend
pip install etcd3                      # etcd backend
pip install python-consul              # consul backend
pip install requests                   # api backend
```

## Parameters

| Parameter           | Required | Default      | Applies to      | Description |
|---------------------|:--------:|--------------|-----------------|-------------|
| **role_name**       | **Yes**  | N/A          | all             | Role identifier used to locate config data. Cannot contain path separators. |
| **backend**         | No       | `filesystem` | all             | Storage backend. One of `filesystem`, `sql`, `redis`, `etcd`, `consul`, `api`. |
| **backend_options** | No       | `null`       | non-filesystem  | Structural backend kwargs. See [backend_options reference](#backend_options-reference). Ignored by `filesystem`. Not `no_log`. |
| **backend_secrets** | No       | `null`       | non-filesystem  | Credentials for the backend, merged over `backend_options`. Marked `no_log`. See the note below. |
| **config_dir**      | No       | role vars dir | filesystem     | Top-level directory to scan. Must exist and be readable. |
| **config_path**     | No       | `null`       | all             | Restrict output to a single location. **Filesystem: must be an absolute path** — a relative value resolves against the Ansible process's working directory, not `config_dir`, and will usually fail as a traversal attempt. SQL/KV: a `/`-delimited location. API: a layer `name`. |
| **config_tag**      | No       | `null`       | all             | Include only configs whose merged data has `config_tag: <value>`. |
| **format**          | No       | `yaml`       | filesystem      | File format: `yaml`, `json`, `ini`. |
| **validate_schema** | No       | `null`       | all             | JSON Schema file to validate merged data against. |
| **track_changes**   | No       | `false`      | filesystem      | Track checksum changes between runs and report `changed: true`. Fails on non-filesystem backends. |
| **dry_run**         | No       | `false`      | all             | Report which sources would contribute, with `data: {}`. See [dry_run](#dry-run-and-check-mode). |

> **Secrets go in `backend_secrets`, not `backend_options`.** Ansible
> implements `no_log` by substring-scrubbing every string *and number* it
> contains out of the module's output. If structural options were marked
> `no_log`, a numeric entity id of `5` would turn every returned config
> value containing a "5" into `VALUE_SPECIFIED_IN_NO_LOG_PARAMETER` and
> change its type from int to string. Keeping credentials in a separate
> `backend_secrets` dict confines the redaction to actual secrets. The two
> dicts are merged before the backend factory is called, and a key may not
> appear in both.

## backend_options reference

`backend_options` and `backend_secrets` are merged and passed as keyword
arguments to the selected backend's factory. The split is purely about
`no_log`: put credentials in `backend_secrets`, everything else in
`backend_options`.

| Backend | Belongs in `backend_secrets` |
|---------|------------------------------|
| `sql`   | `dsn` (carries the password) |
| `redis` | `url` (carries the password) |
| `etcd`  | any credential kwarg the client takes |
| `consul`| `token` |
| `api`   | `auth_token`, or `auth` as `[user, password]` |

Every option each built-in accepts:

### `filesystem`

Takes **no** `backend_options` — the module builds this backend itself from
`config_dir` and `format`. Anything you put here is ignored.

### `sql`

| Option            | Default         | Description |
|-------------------|-----------------|-------------|
| `dsn`             | *required*      | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host/db`. Install the matching driver. |
| `table`           | `role_configs`  | Table name. Must match `^[A-Za-z_][A-Za-z0-9_]*$`. |
| `role_column`     | `role_name`     | Column holding the role identifier. Same regex. |
| `location_column` | `location`      | Column holding the `separator`-delimited path. Same regex. |
| `data_column`     | `data`          | Column holding the JSON payload. Same regex. |
| `separator`       | `/`             | Path separator within `location`. Must be non-empty. |

The table is yours to create; the backend only reads it:

```sql
CREATE TABLE role_configs (
    role_name  VARCHAR(128) NOT NULL,
    location   VARCHAR(255) NOT NULL,   -- e.g. 'global/production/eu-west'
    data       TEXT         NOT NULL,   -- JSON object
    PRIMARY KEY (role_name, location)
);
```

`location` is a path, not a foreign key: ancestors of `global/production/eu-west`
are `global` and `global/production`. Rows for absent levels are simply skipped.

### `redis`

| Option      | Default                      | Description |
|-------------|------------------------------|-------------|
| `url`       | `redis://localhost:6379/0`   | Passed to `redis.from_url`. Include the password as `redis://:pw@host:port/db`. |
| `prefix`    | `""`                         | Key namespace. |
| `separator` | `/`                          | Key path separator. |
| *(others)*  | —                            | Any extra keys go to `redis.from_url` (e.g. `socket_timeout`, `ssl`). |

### `etcd`

| Option      | Default       | Description |
|-------------|---------------|-------------|
| `host`      | `localhost`   | etcd host. **There is no `url` option** — do not copy the Redis form. |
| `port`      | `2379`        | etcd client port. |
| `prefix`    | `""`          | Key namespace. |
| `separator` | `/`           | Key path separator. |
| *(others)*  | —             | Any extra keys go to `etcd3.client` (e.g. `ca_cert`, `timeout`). |

### `consul`

| Option      | Default       | Description |
|-------------|---------------|-------------|
| `host`      | `127.0.0.1`   | Consul agent host. **No `url` option.** |
| `port`      | `8500`        | Consul HTTP port. |
| `prefix`    | `""`          | Key namespace. |
| `separator` | `/`           | Key path separator. |
| *(others)*  | —             | Any extra keys go to `consul.Consul` (e.g. `token`, `scheme`). |

For all three KV stores the full key is
`{prefix}{sep}{role_name}{sep}{location}`, and `prefix` may be given with or
without a trailing separator.

### `api`

| Option           | Default         | Description |
|------------------|-----------------|-------------|
| `api`            | `null`          | Name of a [preset adapter](#api-presets) that discovers the layer chain. |
| `base_url`       | `null`          | Root URL for the preset. Required with `api`, rejected without it. |
| `host`           | —               | Target the preset resolves (all three built-ins take it). Adapter-specific. |
| `layers`         | see note        | Your own layer specs, lowest precedence first. See below. |
| `preset_options` | `{}`            | Alternative place to pass adapter options, if a name would clash with one of the backend's own. |
| *any other key*  | —               | With `api`, passed to the adapter (an unknown one is rejected). Without it, substituted into your own layer templates; string values may not contain `{` or `}`, and a key no layer references is an error. |
| `excludes`       | `[]`            | Layer names to leave out. Everything else is fetched. Naming an unknown layer is an error. |
| `headers`      | `{}`            | Headers sent with every layer; a layer's own `headers` win on collision. |
| `auth_token`   | `null`          | Sugar: sets `{auth_header}: {auth_scheme} {auth_token}`. |
| `auth_header`  | `Authorization` | Header the token goes into. |
| `auth_scheme`  | `Bearer`        | Prefix before the token. Set to `""` for a bare token. |
| `auth`         | `null`          | `[user, password]` for HTTP Basic, as an alternative to `auth_token`. |
| `timeout`      | `10.0`          | Per-request timeout in seconds. |
| `verify_tls`   | `true`          | TLS certificate verification. |
| `allowed_hosts`  | `null`          | Hostname allowlist. Hostname only — no scheme, no port. Empty/absent means no restriction. |

Supply `api`, `layers`, or both — at least one is required. With both, your
layers are appended after the discovered chain and therefore win.

Each entry in `layers` is a dict:

| Field              | Default   | Description |
|--------------------|-----------|-------------|
| `url`              | *required*| Request URL template. Receives `{role_name}`, `{location}` (the layer's own name), and every other option passed to the backend. |
| `name`             | index     | Stable layer identifier, used for ancestry, `config_path`, and provenance. Must be unique. |
| `params`           | `{}`      | Query parameters; values are templates. |
| `headers`          | `{}`      | Per-layer headers; values are templates. |
| `data_path`        | `null`    | Dot-separated path into the JSON response, e.g. `payload.parameters`. Omit to use the top-level object. |
| `list_name_key`    | `null`    | If set, the value at `data_path` must be a list, and is projected to `{item[list_name_key]: item[list_value_key]}`. |
| `list_value_key`   | `value`   | Value key for that projection. |

## API presets

A layer chain cannot be described generically in YAML: every product names its
entities differently, nests them differently, and answers with a different
payload shape — and working out which levels even *exist* for a given target
usually needs a lookup of its own. So a preset is a small **Python adapter**
for one product's API, not a static list of URLs.

You name the API and the target; the adapter does the rest:

```yaml
backend: api
backend_options:
  api: foreman
  base_url: https://foreman.example.com
  host: "{{ inventory_hostname }}"
backend_secrets:
  auth: ["admin", "{{ foreman_password }}"]
```

The `foreman` adapter reads `/api/v2/hosts/<host>` once, sees which
organization, location, domain, subnet, and hostgroup that host belongs to,
and builds the chain from exactly those. Nothing about the hierarchy is
declared in the playbook, and a host with no subnet simply has no subnet
level.

| Preset    | Chain (lowest precedence first) | Discovered from |
|-----------|--------------------------------|-----------------|
| `foreman` | common → organization → location → domain → subnet → hostgroup → host | `/api/v2/hosts/<host>` |
| `awx`     | inventory → group(s) → host | `/api/v2/hosts/?name=<host>`, then its groups |
| `netbox`  | device *or* virtual_machine (server-rendered) | `/api/dcim/devices/?name=<host>`, falling back to virtual machines |

All three take a `host` option naming the target, plus an optional
`api_version`; `foreman` also takes `per_page` (default `all`).

### Filtering with excludes

You get every level the adapter discovered. To drop one, name it:

```yaml
backend_options:
  api: foreman
  base_url: https://foreman.example.com
  host: "{{ inventory_hostname }}"
  excludes: [domain, hostgroup]
```

Excluding a layer the target does not have is an error, and so is excluding
all of them — the message lists the layers that were actually discovered. An
option the adapter does not recognize is rejected the same way, which catches
a misspelled name rather than letting it pass silently.

### Extending a preset

Supplying `layers` alongside `api` appends them *after* the discovered chain,
so they take precedence over every vendor level:

```yaml
backend_options:
  api: foreman
  base_url: https://foreman.example.com
  host: "{{ inventory_hostname }}"
  layers:
    - name: site_override
      url: https://cmdb.internal/hosts/{role_name}/overrides
```

### Writing an adapter

Subclass `ApiPreset` and implement `build_layers`. It receives a `fetch`
callable — a GET returning decoded JSON, or `None` on 404 — that already
carries the backend's auth, timeout, TLS setting, and host allowlist, so an
adapter can look up whatever it needs to decide which levels exist:

```python
from ansible_collections.devitops.ansible.plugins.module_utils \
    .read_config_core.api_presets import ApiPreset, register_api_preset

class MyVendorPreset(ApiPreset):
    product = "myvendor"

    def build_layers(self, fetch):
        self.reject_unknown_options(("host", "api_version"))
        host = self.option("host", required=True)
        prefix = f"{self.base_url}/api/{self.option('api_version', 'v1')}"

        record = fetch(f"{prefix}/nodes/{host}")
        if record is None:
            raise ValueError(f"myvendor: no node {host!r}")

        layers = []
        if record.get("tenant_id") is not None:
            layers.append({
                "name": "tenant",
                "url": f"{prefix}/tenants/{record['tenant_id']}/config",
            })
        layers.append({"name": "node", "url": f"{prefix}/nodes/{record['id']}/config"})
        return layers

register_api_preset("myvendor", MyVendorPreset)
```

Return layer specs in the same form as a hand-written `layers` entry, so
`data_path`, `list_name_key`, and `params` are all available. Registration
must happen before the module builds its argument spec — see the note at the
end of [Development and testing](#development-and-testing).

`available_api_presets()` lists what is registered; an unknown name fails with
the known names in the message.

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
      table: role_configs
    backend_secrets:
      dsn: "postgresql+psycopg://user:pass@db.example.com/appdb"
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
      prefix: configs
    backend_secrets:
      url: redis://redis.example.com:6379/0
    config_path: "production"
  register: redis_configs
  delegate_to: localhost
```

### etcd

```yaml
- name: Read from etcd (keys under configs/myrole/...)
  devitops.ansible.read_config:
    role_name: myrole
    backend: etcd
    backend_options:
      host: etcd.example.com     # note: host/port, not url
      port: 2379
      prefix: configs
    config_path: "global/production"
  register: etcd_configs
  delegate_to: localhost
```

### Consul

```yaml
- name: Read from the Consul KV store
  devitops.ansible.read_config:
    role_name: myrole
    backend: consul
    backend_options:
      host: consul.example.com   # note: host/port, not url
      port: 8500
      prefix: configs
    backend_secrets:
      token: "{{ consul_token }}"
    config_path: "global/production"
  register: consul_configs
  delegate_to: localhost
```

### API — Foreman preset

```yaml
- name: Read merged Foreman parameters for this host
  devitops.ansible.read_config:
    role_name: myrole
    backend: api
    backend_options:
      api: foreman
      base_url: https://foreman.example.com
      host: "{{ inventory_hostname }}"
      allowed_hosts: ["foreman.example.com"]
    backend_secrets:
      auth: ["admin", "{{ foreman_password }}"]
    config_path: host
  register: foreman_config
  delegate_to: localhost
```

The adapter reads `/api/v2/hosts/{{ inventory_hostname }}` once, then fetches
a parameters endpoint for each entity that host belongs to:

```text
/api/v2/common_parameters
/api/v2/organizations/3/parameters
/api/v2/locations/7/parameters
/api/v2/domains/5/parameters
/api/v2/hostgroups/11/parameters
/api/v2/hosts/42/parameters
```

This host has no subnet, so no subnet level appears — that requires no
configuration. Each endpoint answers
`{"results": [{"name": ..., "value": ...}]}`, and the adapter merges them
lowest precedence first.

Token auth instead of Basic:

```yaml
    backend_secrets:
      auth_token: "{{ foreman_personal_access_token }}"
```

### API — AWX / Ansible Tower preset

```yaml
- name: Read merged AWX variables for this host
  devitops.ansible.read_config:
    role_name: myrole
    backend: api
    backend_options:
      api: awx
      base_url: https://awx.example.com
      host: "{{ inventory_hostname }}"
      allowed_hosts: ["awx.example.com"]
    backend_secrets:
      auth_token: "{{ awx_oauth_token }}"
    config_path: host
  register: awx_config
  delegate_to: localhost
```

Finds the host, reads its inventory and group memberships, and merges
`variable_data` in Ansible's own precedence: inventory → group(s) → host.
Group layers are named `group:<name>`, so a specific one can be excluded:

```yaml
      excludes: ["group:staging"]
```

### API — NetBox preset

```yaml
- name: Read the rendered NetBox config context for this device
  devitops.ansible.read_config:
    role_name: myrole
    backend: api
    backend_options:
      api: netbox
      base_url: https://netbox.example.com
      host: "{{ inventory_hostname }}"
      allowed_hosts: ["netbox.example.com"]
    backend_secrets:
      auth_token: "{{ netbox_token }}"
    config_path: device        # or virtual_machine
  register: netbox_config
  delegate_to: localhost
```

NetBox merges config contexts server-side, so the chain is a single layer.
The adapter looks for a device with that name and falls back to a virtual
machine, naming the layer `device` or `virtual_machine` accordingly.

### API — layered REST endpoints

```yaml
- name: Read merged parameters from a REST API
  devitops.ansible.read_config:
    role_name: myrole
    backend: api
    backend_secrets:
      auth_token: "{{ api_token }}"
    backend_options:
      timeout: 10
      # Optional allowlist: refuse to fetch from any host not in this list.
      # Defense-in-depth against template-injection redirecting
      # requests to an attacker-controlled endpoint.
      allowed_hosts: ["api.example.com"]
      organization_id: 3
      host_id: 42
      layers:
        - name: organization
          url: "https://api.example.com/v1/organizations/{organization_id}/parameters"
          params: {per_page: "all"}
          data_path: "results"
          list_name_key: "name"
        - name: host
          url: "https://api.example.com/v1/hosts/{host_id}/parameters"
          params: {per_page: "all"}
          data_path: "results"
          list_name_key: "name"
    config_path: host
  register: http_configs
  delegate_to: localhost
```

## API semantics

The API backend behaves differently enough from the path-based backends to
be worth stating explicitly:

- **`config_path` is a layer `name`**, not a path. Naming an excluded layer
  raises, and the error lists both the applicable and the configured layer
  names.
- **`discover()` returns only the deepest applicable layer.** Multi-mode
  therefore yields exactly **one** entry — the full-chain merge — keyed by
  that layer's name. Use single mode with `config_path: <layer>` for a
  partial view.
- **Every configured layer is fetched unless named in `excludes`.** That is
  how you model "this host has no hostgroup". A layer whose URL template needs
  a value you did not supply is a hard error naming the layer and the key —
  never a silently dropped level. Supplying a value no layer references is an
  error too, which catches a misspelled option name.
- **404 means "this level has no config"** and is merged as absent. Any
  other non-2xx status raises and fails the task — a 401 is not mistaken
  for an empty layer.
- **Responses are cached per `(url, query params)` for the lifetime of one
  module invocation.** Layers sharing an endpoint cost one request. The
  cache does not persist across tasks or plays.
- **Fingerprint** is the server's `ETag` when present, otherwise a SHA-256
  of the response body.
- **`{role_name}` only scopes a layer if the template references it.** A URL
  with no `{role_name}` returns the same data for every role.

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

## Operational notes

### Change tracking

`track_changes: true` is **filesystem-only**; any other backend fails the
task. Its contract:

- Checksums are stored in `.<role_name>_checksums.json`, written **inside
  `config_dir`**. That directory must be writable, and the file is not
  covered by a typical `.gitignore` — add it if `config_dir` is in a repo.
- The **first run always reports `changed: true`**, because there is no
  previous checksum file to compare against.
- The file is only rewritten when something actually changed.
- Only files read during *this* run are hashed, so the recorded set depends
  on whether you ran in single or multi mode.
- `dry_run: true` suppresses change tracking entirely.

### Dry run and check mode

`dry_run: true` reports which sources *would* contribute, and returns
`data: {}` for every entry — it never merges. Schema validation is skipped.
For the filesystem backend it uses a cheap `isfile` probe, but for SQL, KV,
and API backends `exists()` still performs a query or request, so a dry run
is not a zero-cost operation against a remote store.

> **Check mode is declared but not honored.** The module sets
> `supports_check_mode=True`, yet `track_changes` still writes
> `.<role_name>_checksums.json` and still reports `changed: true` under
> `--check`. Until that is fixed, do not assume `--check` leaves
> `config_dir` untouched.

### Schema validation

`validate_schema` runs against **every** merged config before `config_tag`
filtering, so a config you are about to filter out can still fail the task.
It is skipped entirely when `dry_run: true`. The path must be a regular
file — FIFOs, device nodes, and symlinks to them are rejected.

### Locating `config_dir` automatically

If `config_dir` is omitted for the filesystem backend, the module looks for
a role `vars/` directory by reading an `ansible.cfg`, in this order:
`$ANSIBLE_CONFIG`, then `$ANSIBLE_HOME/ansible.cfg`, then
`$HOME/ansible.cfg`. It reads `defaults.roles_path`, splits on `:`, and
returns the first `<roles_path>/<role_name>/vars` that exists. This is a
narrower search than ansible-core's own config precedence (it does not
consult `./ansible.cfg` or `/etc/ansible/ansible.cfg`), and any error is
swallowed into "not found". Passing `config_dir` explicitly is more
predictable.

### INI format

INI files parse to `{section: {key: value}}`. Three consequences: **all
values are strings** (`workers = 8` yields `"8"`), keys in `[DEFAULT]` are
replicated into every other section rather than appearing at the top level,
and a file with keys before any `[section]` header fails to parse.

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

- **Secrets are never logged.** `backend_secrets` carries DSNs, auth
  tokens, and Basic-auth credentials; the argument spec marks it
  `no_log=True`, so Ansible redacts it from task logs, callbacks, and
  `--verbose` output. `backend_options` is deliberately *not* marked: it
  holds only structural values, and marking it would let Ansible's
  substring scrubbing corrupt the returned configuration.
- **Filesystem path traversal:** every resolved path must lie inside
  `config_dir`. `..` segments and sibling-prefix paths raise `ValueError`.
  **Symlinks are not resolved.** `validate_path_security` compares
  `os.path.abspath` values, which normalizes `..` but does not follow
  links, so a symlink *inside* `config_dir` pointing outside it is
  followed and its target read. Treat `config_dir` as trusted: if
  untrusted users can create symlinks in it, they can read arbitrary
  files the Ansible process can read.
- **Role-name hygiene:** role names may not contain path separators
  (`/`, `\`, `os.sep`).
- **SQL injection:** the SQL backend validates the table and the three
  column names against `^[A-Za-z_][A-Za-z0-9_]*$` before interpolating
  them, and runtime values (`role_name`, `location`) use SQLAlchemy bound
  parameters. `separator` is only checked for non-emptiness — it is never
  interpolated into SQL, so it is not an injection vector, but it is not
  regex-validated either. The `dsn` property returns SQLAlchemy's
  password-redacted URL form, so it is safe to surface in error output.
- **API SSRF & template injection:**
  - Context values containing `{` or `}` are rejected at construction time
    to block Python format-string gadgets (e.g.
    `{__class__.__mro__}`).
  - Configure `allowed_hosts: [...]` in `backend_options` to pin outbound
    requests to a hostname allowlist. Requests to any other host fail
    before the wire call. The comparison is **hostname only**,
    case-insensitive — not scheme, not port. List bare hostnames
    (`api.example.com`), never `api.example.com:8443`, which matches
    nothing and blocks every request.
  - Only **string** template values are checked for `{`/`}`. Non-string
    values (ints, lists, dicts) pass through unvalidated; they are
    stringified by `str.format` and not re-expanded, so they are not a
    format-string gadget, but do not rely on this as sanitization.
  - `verify_tls` is `True` by default.
- **Schema files:** `validate_schema` rejects non-regular-file paths (no
  FIFOs/devices) and catches structurally-invalid merged configs before
  they are consumed downstream.
- **Delegate network backends:** SQL / Redis / etcd / Consul / HTTP
  typically run against a central store reachable from the controller,
  not each target host. Use `delegate_to: localhost` unless the target
  host actually needs to reach the backend directly.

## Development and testing

The repo has a pytest-based test suite (342 tests, ~96% coverage) plus a
subprocess-based integration suite that invokes the module as a real
Ansible subprocess.

```bash
make venv            # create .venv and install dev deps
make test            # unit tests only (fast)
make integration     # integration tests only
make test-all        # unit + integration + live
make coverage        # unit tests with terminal coverage report
make coverage-html   # HTML coverage at htmlcov/index.html
```

Layout:

- `tests/unit/` — per-backend unit tests plus the `BackendContract`
  conformance suite (and its two mixins, `ValidatesTargetsContract` and
  `ContentAwareDiscoveryContract`).
- `tests/integration/` — module-as-subprocess end-to-end tests.
- `tests/live/` — every backend against a **real server** in a Podman
  container. See below.

### Adding a backend

There are two extension points, and the cheaper one is usually the right one.

**If your store is key-value shaped**, implement the three-method `KVClient`
Protocol — `get(key)`, `keys_with_prefix(prefix)`, `revision(key)` — and hand
it to the existing `KVBackend`, which supplies all the hierarchy and merge
logic. This is how the Redis, etcd, and Consul backends are built; each
adapter is under 60 lines. Return `None` from `revision()` if your store has
no native versioning and `KVBackend` will fall back to hashing the value.

```python
from read_config_core.kv import KVBackend
from read_config_core.registry import register_backend

class MyKVClient:
    def get(self, key): ...
    def keys_with_prefix(self, prefix): ...
    def revision(self, key): return None

def make_my_backend(host="localhost", prefix="", separator="/", **kw):
    return KVBackend(MyKVClient(...), prefix=prefix, separator=separator)

register_backend("mystore", make_my_backend)
```

**Otherwise**, implement the six-method `ConfigBackend` Protocol —
`discover`, `resolve_ancestry`, `load`, `exists`, `fingerprint`, `identify`
— and register the class directly.

Either way, subclass `BackendContract` in your tests for free conformance
coverage, adding `ValidatesTargetsContract` if your backend rejects malformed
targets and `ContentAwareDiscoveryContract` if `discover()` inspects stored
data. `InMemoryKVClient` is available as a dict-backed fake for your own tests.

> `register_backend` overwrites any existing entry under the same name. Note
> that the module's `backend` choices are computed from `available_backends()`
> when `run_module()` builds its argument spec, so a backend registered by a
> third-party collection is only selectable if the registration has already
> run by then — importing `read_config_core` alone does not do it.

### Live backend tests

The unit suite fakes the remote stores. The live suite runs each one for
real — PostgreSQL, MariaDB, Redis, etcd, Consul, and a REST API — in
Podman containers, and drives them from the host with the same client
libraries a production run would use. Nothing executes *inside* a
container; they are servers only.

```bash
make live-install    # client libraries for the live suite
make live-up         # start all six services (parallel) and wait for readiness
make live            # run tests/live against them
make live-status     # per-service state and readiness
make live-errors     # error lines found in container logs
make live-down       # stop and remove everything (the database is kept)
make live-reset      # same, and wipe the database to force a clean rebuild
```

Containers are started once and reused, so repeated `make live` runs pay no
startup cost — which matters here, because a cold start runs three products'
database migrations. Budget several minutes for the first `make live-up`; the
suite itself finishes in about forty seconds.

| Service    | Image                   | Port    | Exercises |
|------------|-------------------------|---------|-----------|
| PostgreSQL | `postgres:17-alpine`    | `15432` | `sql` backend |
| MariaDB    | `mariadb:12.3`          | `13306` | `sql` backend, second dialect |
| Redis      | `redis:8.10-alpine`     | `16379` | `redis` backend, password auth |
| etcd       | `etcd:v3.7.1`           | `12379` | `etcd` backend, `mod_revision` |
| Consul     | `consul:1.22.7`         | `18500` | `consul` backend, `ModifyIndex` |
| nginx      | `nginx:1.29-alpine`     | `18080` | `api` backend, bearer auth, real `ETag`s |

Ports are deliberately non-default so the suite never collides with a
locally installed Postgres or Redis, and every service binds to
`127.0.0.1` only.

The centrepiece is `tests/live/test_live_parity.py`: one three-level
hierarchy is seeded into all six backends *plus* the filesystem, merged
through the same `MergeEngine`, and asserted to produce byte-identical
output. That is the README's central claim — pluggable storage, fixed
merge semantics — checked rather than asserted.

**All three presets are tested against the real products**, not fixtures of
them. Each container migrates and seeds itself, and the suite then creates
the entity hierarchy the adapter walks:

- **Foreman** — an organization/location/domain/hostgroup/host chain with
  parameters at each level. The seeded host deliberately has no subnet, so
  the adapter's "a level the target lacks is simply absent" behaviour is
  exercised against Foreman actually returning `subnet_id: null`.
- **NetBox** — a device plus two config contexts at different weights, so
  what the adapter reads is NetBox's own server-side merge.
- **AWX** — an inventory → group → host chain with variables at each level.

PostgreSQL's data directory is bind-mounted to `tests/live/.pgdata` on the
host, so those migrations run **once**. A first `up` against an empty volume
takes about five and a half minutes and applies ~1,900 migrations creating
~580 tables across the three products; a later `up` reuses the schema, applies
none, and is back in about a minute. `make live-reset` wipes the volume when
you want a clean rebuild. The directory is owned by the container's `postgres`
uid as mapped into your user namespace, so it is not readable directly — use
`podman unshare` if you need to inspect it.

The app containers still run their initialization on every start; it is
idempotent and becomes a no-op once the schema is current. `max_connections`
is raised to 300, since Foreman alone holds around a dozen connections at idle
and four databases share the one server. Two quirks worth knowing, both found by
running the real thing: Foreman returns parameter values as **strings**
(`workers == "8"`), while AWX and NetBox preserve JSON types; and NetBox 4.6
issues **v2 API tokens** presented as `Bearer nbt_<key>.<token>`, which needs
`API_TOKEN_PEPPER_1` set to at least 50 characters.

AWX is served by Django directly rather than through its bundled web stack,
whose nginx wants port 80 — unavailable under rootless podman. Only the read
API is exercised, so that makes no difference to what is tested.

> **Switching branches with the fleet up breaks the nginx fixtures.** A
> checkout replaces the mounted directory's inodes, and the container keeps
> serving the old ones — a healthy server that 404s everything. The readiness
> probe fetches a real fixture rather than a health endpoint so
> `containers.sh status` reports this as *not ready* instead of letting it
> surface as a couple of dozen puzzling test failures. Recreate the container
> (`podman rm -f rclive-nginx && make live-up`) after a checkout.

Container logs are scanned for `FATAL`/`PANIC`/`ERROR` lines, scoped to
the current pytest session. A test that provokes a server error on
purpose declares it via the `expect_container_error` fixture; anything
else fails the run.

`make test-all` runs all three suites — unit, integration, and live. If the
containers are not up, the live tests skip with a pointer to `make live-up`,
and the target prints a notice so a green run cannot be mistaken for one that
actually exercised the backends. Set `RC_LIVE_REQUIRE=1` to turn that skip
into a hard failure instead (for CI).

`make test` stays unit-only and needs no Podman, which keeps the inner loop
at about a second.
