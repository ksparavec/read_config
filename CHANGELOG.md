# Changelog

All notable changes to the `devitops.ansible` collection will be
documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added
- **API presets.** `backend_options.api` selects a Python adapter for a known
  product's configuration API. The adapter looks the target up and builds the
  layer chain from the levels that target actually belongs to, so nothing
  about the hierarchy is written in YAML:

  ```yaml
  backend: api
  backend_options:
    api: foreman
    base_url: https://foreman.example.com
    host: "{{ inventory_hostname }}"
    excludes: [domain]        # optional
  backend_secrets:
    auth: ["admin", "{{ foreman_password }}"]
  ```

  Ships with `foreman` (common → organization → location → domain → subnet →
  hostgroup → host, resolved from `/api/v2/hosts/<host>`), `awx` (inventory →
  group(s) → host), and `netbox` (server-rendered config context for a device
  or virtual machine). A level the target does not have is simply absent —
  a host with no subnet needs no declaration. Hand-written `layers` still work
  and can be appended to an adapter's chain. Write your own by subclassing
  `ApiPreset` and calling `register_api_preset()`.

### Changed
- **BREAKING: the API backend drops `context` and selects layers by
  exclusion.** Both `backend_options.context` and `ApiLayer.required_context`
  are gone. Presets discover their own chain, and every discovered layer is
  merged unless named in `excludes`.

  Previously a layer was active only if every key in its `required_context`
  appeared in `context`, so a forgotten or misspelled id silently dropped a
  whole precedence level while the task still reported success. Now the
  hierarchy is not enumerated by hand at all, and the remaining mistakes fail
  loudly: an option the adapter does not recognize is rejected, and so is an
  unknown name in `excludes` or excluding every layer.

  ```yaml
  # before                              # after
  context:                              host: "{{ inventory_hostname }}"
    organization_id: 3                  excludes: [domain]
    host_id: 42
  ```

  For a hand-written `layers` list, values referenced by the templates are
  passed as plain `backend_options` keys.
- **BREAKING: credentials move to a new `backend_secrets` option.**
  `backend_options` is no longer `no_log`. Ansible implements `no_log` by
  substring-scrubbing every string *and number* in the marked value out of the
  module's output, which corrupted the returned configuration: with a numeric
  context id of `5`, the values `5`, `50`, and `8500` all came back as
  `VALUE_SPECIFIED_IN_NO_LOG_PARAMETER` with their type changed from int to
  string, and `files_merged` came back as `kv://********/webapp/global`.
  Credentials (`dsn`, `url`, `auth_token`, `auth`, `token`) now go in
  `backend_secrets`, which is `no_log`; everything structural stays in
  `backend_options`. The two are merged before the backend factory is called
  and a key may not appear in both. Found by the live suite.
- **Renamed the HTTP backend to the API backend.** `backend: http` is now
  `backend: api`; `HTTPBackend` is `ApiBackend`, `HTTPLayer` is `ApiLayer`,
  and `read_config_core/http.py` is `read_config_core/api.py`. The name
  describes what it models — an ordered chain of REST endpoints — rather than
  the transport it happens to use. No alias is kept: nothing had been
  published against the old name.
## [1.0.1] - 2026-04-17

### Security
- **CRITICAL:** `backend_options` is now marked `no_log=True`. Prior
  versions logged database DSNs (including passwords), HTTP auth tokens,
  and Basic auth credentials in verbose task output.
- **HIGH:** `HTTPBackend` rejects context values containing `{` / `}`
  to block Python format-string gadget exploitation, and accepts an
  optional `allowed_hosts` allowlist that pins outbound requests to a
  configured set of hostnames (defense-in-depth against SSRF).
- **MEDIUM:** `SQLBackend.dsn` now returns SQLAlchemy's
  password-redacted URL form instead of the plaintext URL.
- **LOW:** `validate_against_schema` refuses paths that are not regular
  files (blocks symlink-to-pipe / device-node surprises).

### Fixed
- `DOCUMENTATION` string now parses under `ansible-doc -t module
  devitops.ansible.read_config` (the embedded `C(config_tag: <value>)`
  token previously broke the Ansible documentation parser).
- `tests/test_read_config_playbook.yml` YAML syntax errors on Jinja
  assertions using `'foo' in bar` patterns, and bare `read_config:`
  references updated to the FQCN `devitops.ansible.read_config:`.
- Three unused imports flagged by ruff in the test suite.

### Added
- `allowed_hosts` constructor argument on `HTTPBackend` for hostname
  pinning.
- 16 new unit tests covering the security hardenings and the
  `track_changes`-with-non-filesystem-backend rejection path.
- `IMPROVEMENTS.md` with a prioritized backlog of future features.

## [1.0.0] - 2026-04-16

### Added
- First released version as an Ansible Galaxy collection.
- `read_config` module with a pluggable backend architecture.
- Six built-in backends:
  - `filesystem` — YAML/JSON/INI files with directory-hierarchy merging.
  - `sql` — SQLAlchemy-backed relational storage, path-based hierarchy.
  - `redis` — Redis key-prefix hierarchy, SHA-256 content fingerprints.
  - `etcd` — etcd v3 key-prefix hierarchy, `mod_revision` fingerprints.
  - `consul` — Consul KV hierarchy, `ModifyIndex` fingerprints.
  - `http` — layered REST API model with templated URLs/params/headers,
    API-token auth sugar, ETag fingerprinting.
- `ConfigBackend` Protocol, `MergeEngine`, and `register_backend` API for
  third-party backends.
- Full pytest suite: 326 tests, 0 skipped, ~96% coverage.
- Integration suite invoking the module as a real Ansible subprocess.

### Fixed
- Merge bug in pre-collection code where parent configs were silently
  discarded because `dict_merge`'s return value was ignored.
