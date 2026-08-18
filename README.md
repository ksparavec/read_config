# read_config

> An Ansible module for role configuration that varies by environment,
> datacenter, or host — assembled from a hierarchy of defaults and
> overrides, out of whichever storage you happen to keep it in.

Ships as the `devitops.ansible` collection.

## The problem

Say you have a `webapp` role. It needs two worker processes in staging and
eight in production. The EU datacenter talks to a different database host
than the US one. The connection pool is 10 by default, 50 in production, but
40 in EU because that database is smaller. Everything else — the listen port,
the log format, the health-check path — is the same everywhere.

So you have shared defaults, and a chain of increasingly specific overrides
on top of them. Nothing about that is exotic; it is how most infrastructure
configuration is actually shaped.

Ansible gives you `group_vars`, `host_vars`, `vars_files`, and Jinja
conditionals, and for a while they are enough. Three things tend to push past
them:

**Nested values don't merge the way you want.** Two `group_vars` files that
both define `database:` don't combine — the higher-precedence one replaces
the other outright, and your carefully-set `pool_size` disappears along with
it. You can reach for the `combine(recursive=True)` filter, but then you are
hand-writing the precedence chain in every role that needs it, and it stays
correct only as long as everyone writes it the same way. (`hash_behaviour =
merge` fixes the merge, but applies globally to every variable in the run,
which is why it is discouraged.)

**The hierarchy is inventory-shaped.** `group_vars` precedence follows your
inventory groups. If your real override order is *global → environment →
datacenter → host*, you can usually contort inventory into that shape — but
the contortion is the point: you end up encoding a configuration hierarchy
in a structure designed for host grouping.

**The data may not live in the repo.** Once configuration is edited by people
who don't send pull requests, it tends to migrate into a database, a config
server, Consul, or whatever your provisioning system already exposes over
REST. At that point `group_vars` isn't in the conversation at all.

`read_config` addresses all three. You give it a role name and a location in
a hierarchy; it walks from the root down to that location, deep-merges every
level it finds, and returns one dict plus the list of exactly which sources
contributed to it.

The storage backend is pluggable. The merge behaviour is not — that is the
whole idea. Start with YAML files in the repo, and if the data later moves
into Postgres or Consul, the merge semantics your roles depend on move with
it unchanged.

## What it does

Given this tree:

```
configs/
├── webapp.yaml                    # workers: 2, log_level: info
│                                  # database: {pool_size: 10, host: db.default}
└── production/
    ├── webapp.yaml                # workers: 8, log_level: warn
    │                              # database: {pool_size: 50}
    └── eu-west/
        └── webapp.yaml            # database: {pool_size: 40, host: db.eu-west}
```

Asking for `production/eu-west`:

```yaml
- devitops.ansible.read_config:
    role_name: webapp
    config_dir: /srv/configs
    config_path: /srv/configs/production/eu-west
```

gives you:

```yaml
workers: 8                   # production overrode the default
log_level: warn              # production overrode the default
database:
  pool_size: 40              # eu-west beat production, which beat the default
  host: db.eu-west           # eu-west overrode the default
```

Look at `database`: it is defined at all three levels, and the result is
their union rather than whichever one won. That recursive merge is the
behaviour that is awkward to get out of `group_vars` alone.

Every result also carries the ordered list of files that produced it, so
"where did this value come from?" has an answer.

## Quick start

Install the collection:

```bash
ansible-galaxy collection install devitops.ansible
```

Create a small hierarchy. The file name must match the role name:

```bash
mkdir -p configs/production
cat > configs/webapp.yaml <<'EOF'
listen_port: 8080
workers: 2
database:
  pool_size: 10
  host: db.default.internal
EOF
cat > configs/production/webapp.yaml <<'EOF'
workers: 8
database:
  pool_size: 50
EOF
```

Read it back:

```yaml
# site.yml
- hosts: localhost
  gather_facts: false
  tasks:
    - name: Load the merged webapp config for production
      devitops.ansible.read_config:
        role_name: webapp
        config_dir: "{{ playbook_dir }}/configs"
        config_path: "{{ playbook_dir }}/configs/production"
      register: cfg

    - name: Show the result
      ansible.builtin.debug:
        var: cfg.ansible_facts.read_config.configs.values() | first
```

```bash
ansible-playbook site.yml
```

You get the merged configuration, plus its provenance:

```yaml
meta:
  files_merged:
    - /path/to/configs/webapp.yaml
    - /path/to/configs/production/webapp.yaml
data:
  listen_port: 8080            # inherited from the root level
  workers: 8                   # overridden by production
  database:
    pool_size: 50              # overridden by production
    host: db.default.internal  # inherited from the root level
```

> One thing to watch: for the filesystem backend, `config_path` must be an
> **absolute** path. A relative value is resolved against the working
> directory of the Ansible process, not against `config_dir`.

Omit `config_path` and you get every location that holds a `webapp.yaml`,
each merged with its own ancestors.

## Features

- **Six storage backends** — `filesystem`, `sql` (any SQLAlchemy database),
  `redis`, `etcd`, `consul`, and `api` for layered REST APIs. Switching
  backends does not change how your configuration merges.
- **Presets for real APIs** — `foreman`, `awx`, and `netbox` ship as Python
  adapters that know their product. You give a base URL and a host; the
  adapter looks it up, works out which levels that host actually belongs to,
  and merges them all. Nothing about the hierarchy goes in the playbook, and
  `excludes` drops any level you don't want. Custom APIs are still described
  by hand, and you can register an adapter of your own.
- **Hierarchical deep merge** — dicts merge recursively; scalars and lists
  follow child-wins precedence; missing levels are skipped silently.
- **Provenance** — every result names the ordered sources that produced it.
- **Schema validation** — check merged output against a JSON Schema before a
  role consumes it.
- **Change tracking** — detect when configuration sources have drifted since
  the last run and report `changed`, so handlers fire.
- **Tag filtering** — return only the configs carrying a given `config_tag`.
- **Dry run** — see which sources *would* contribute, without merging them.
- **Multiple file formats** — YAML, JSON, and INI on the filesystem backend.
- **Extensible** — add a backend by implementing a six-method protocol, or a
  key-value store by implementing three methods and reusing the existing
  hierarchy logic.

## Documentation

[**REFERENCE.md**](REFERENCE.md) has the complete details:

| Looking for | See |
|---|---|
| Every module parameter | [Parameters](REFERENCE.md#parameters) |
| Options for a specific backend | [backend_options reference](REFERENCE.md#backend_options-reference) |
| Talking to Foreman, AWX, or NetBox | [API presets](REFERENCE.md#api-presets) |
| A working example per backend | [Backend-specific examples](REFERENCE.md#backend-specific-examples) |
| How the REST backend models an API | [API semantics](REFERENCE.md#api-semantics) |
| A realistic end-to-end deployment | [Full playbook example](REFERENCE.md#full-playbook-example) |
| Change tracking, dry run, check mode | [Operational notes](REFERENCE.md#operational-notes) |
| Output structure | [Return values](REFERENCE.md#return-values) |
| Threat model and hardening | [Security](REFERENCE.md#security) |
| Writing your own backend | [Development and testing](REFERENCE.md#development-and-testing) |

Runnable examples for every backend — including the Foreman preset — ship
inside the module itself, so they are available offline once the collection
is installed:

```bash
ansible-doc devitops.ansible.read_config          # full docs + examples
ansible-doc -s devitops.ansible.read_config       # a paste-ready task snippet
```

The same examples are in
[Backend-specific examples](REFERENCE.md#backend-specific-examples) if you
would rather read them in the browser.

### Project documents

| Document | What it covers |
|---|---|
| [CHANGELOG.md](CHANGELOG.md) | Released and unreleased changes, including breaking ones |
| [IMPROVEMENTS.md](IMPROVEMENTS.md) | Known issues and the prioritized backlog |
| [REFERENCE.md](REFERENCE.md) | Complete parameter, backend, and security reference |
| [LICENSE.md](LICENSE.md) | MIT |

## Development

```bash
make venv        # create .venv and install dev dependencies
make test        # unit tests only (~1s, no Podman needed)
make test-all    # unit + integration + live tests
make coverage    # coverage report
```

There is also a live suite that runs every backend against a real server in a
Podman container — PostgreSQL, MariaDB, Redis, etcd, Consul, and, for the API
presets, real Foreman 3.16.0, NetBox 4.6.8, and AWX 24.6.1. See
[Development and testing](REFERENCE.md#development-and-testing).

Bug reports and pull requests are welcome at
[github.com/ksparavec/read_config](https://github.com/ksparavec/read_config).

## License

MIT. See [LICENSE.md](LICENSE.md).
