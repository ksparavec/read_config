# Changelog

All notable changes to the `devitops.ansible` collection will be
documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

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
