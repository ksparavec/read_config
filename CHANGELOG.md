# Changelog

All notable changes to the `devitops.ansible` collection will be
documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

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
