# Improvements & Future Features

Backlog of code improvements and new features identified during the
1.0.0 → 1.0.1 review. Items are grouped by intent and ordered within
each section by (impact × reach) / cost. Items marked **DONE** shipped
in 1.0.1; everything else is a candidate for 1.1.0+.

---

## Code quality

### 1. Fix `kv.py` type narrowing on `load()` — _small_

`KVBackend.load` reassigns a `bytes | None` local to `str` after decode:

```python
raw = self._client.get(...)
if isinstance(raw, bytes):
    raw = raw.decode("utf-8")   # mypy: Incompatible types in assignment
return json.loads(raw)
```

Rename the decoded form to a new local (`text`) so the original stays
typed. Zero behavior change; clears the last mypy error in the core.

### 2. Add `types-PyYAML`, `types-requests` to dev deps — _small_

Removes the two "library stubs not installed" mypy hints and enables
real type checking on the cache / HTTP modules.

### 3. Wire mypy / ruff / bandit into CI — _small_

The repo already ships with `pyproject.toml` and tests run clean. Add
a GitHub Actions workflow that runs:

- `make test-all`
- `ruff check plugins/ tests/`
- `mypy plugins/module_utils/read_config_core/ --strict`
- `bandit -r plugins/ -ll`
- `ansible-lint galaxy.yml meta/ plugins/`
- `ansible-test sanity --test validate-modules` against the installed
  collection.

Matrix Python 3.10 / 3.11 / 3.12 and ansible-core 2.15 / 2.16 / 2.17.

### 4. Harden INI parsing path — _small_

`ConfigCache.load_config` returns `{section: dict(...)}` for INI, which
silently drops top-level keys (INI has no "no section"). Consider
rejecting config files without sections, or placing bare keys under a
`DEFAULT` bucket explicitly (operators can choose; today both are a
silent loss).

### 5. Extract `_extensions_for` → `FormatRegistry` — _medium_

Today the filesystem backend hard-codes three formats in `_EXTENSIONS`.
Promote the mapping into a registry (`register_format("toml",
(".toml",), toml.load)`) so third-party formats (TOML, HCL) can be
plugged in without forking the backend.

### 6. Share the `separator`/`prefix` normalization between KV and SQL — _small_

`SQLBackend` and `KVBackend` each implement `resolve_ancestry` by path
splitting on a configurable separator, and each re-implement the
"strip empties + reassemble" loop. Extract a `build_ancestry(target,
sep)` helper in `base.py`.

---

## Testing

### 7. Integration tests for non-filesystem backends — _medium_

Today only the filesystem backend is subprocess-tested end-to-end. Add
in-process integration tests for:

- SQL → use the existing `sqlite:///tmp/…` SQLAlchemy pattern.
- Redis → use `fakeredis` (already a dev dep).
- HTTP → use `requests_mock` (already a dev dep).
- etcd / Consul → wire through a minimal `MagicMock`-based fake (or
  skip in CI unless a local container is available).

This raises confidence that the `run_module()` plumbing around
`get_backend("<name>", **opts)` works for every built-in.

### 8. Property-based test for deep-merge semantics — _medium_

Use `hypothesis` to generate random dict-hierarchies and assert the
classical merge laws: child-wins-on-collision, dicts-recursively-merged,
lists-replaced, order-of-chain-matters. Catches regressions in
`MergeEngine.build` much earlier than example-based tests.

### 9. Golden tests for ETag / mod_revision fingerprint stability — _small_

Capture a snapshot of `{"fingerprints": {...}}` for each backend and
reject any future diff that isn't accompanied by a deliberate "format
change" flag. Makes fingerprint format evolution an explicit, reviewed
action.

### 10. Add a `ValidatesTargetsContract` test for SQL — _small_

`SQLBackend` currently accepts any target string for `resolve_ancestry`
(empty or not). Decide the contract: reject unknown locations (cleaner)
or accept them silently (current). Lock it in with the existing mixin.

---

## Security

### 11. Optional `schema_dir` allowlist for `validate_schema` — _small_

Constrain `validate_schema` paths to live under `config_dir` (or an
explicit `schema_dir`). Today the path is trusted-operator input, but
containing it means a compromised Ansible Vault value can't redirect
the module to read `/etc/ssl/private/*`.

### 12. Redacted `backend_options` echo in diagnostic errors — _small_

`run_module()` embeds `backend_name` in error messages ("Failed to
instantiate backend 'sql': …"). Consider also attaching a **redacted**
view of `backend_options` keys (values masked) so operators know which
option was malformed without exposing the value.

### 13. Per-layer auth in `HTTPBackend` — _medium_

Today `auth` / `auth_token` is backend-wide. Multi-tenant APIs often
need per-layer credentials (org token vs. host token). Allow
`HTTPLayer(auth=...)` to override.

### 14. Optional `requests.Session` with retries / backoff — _small_

Construct a `Session` with an `HTTPAdapter(max_retries=Retry(...))` so
flaky 502/503s don't fail the whole task. Today every GET is isolated
and bails on first non-2xx/404.

---

## Documentation

### 15. Publish API docs (`sphinx` + `autodoc`) — _medium_

The `ConfigBackend` Protocol is a public extension point. A hosted
Sphinx site (ReadTheDocs or GitHub Pages via the existing README
skeleton) makes it discoverable without a repo checkout.

### 16. "Writing a third-party backend" guide — _small_

A 1-page walkthrough that shows (a) implementing the 6-method Protocol,
(b) registering via `register_backend("name", factory)`, (c)
subclassing `BackendContract` for free conformance coverage, and (d)
packaging the new backend as its own collection that depends on
`devitops.ansible`.

### 17. Worked Foreman example — _small_

The HTTP backend's raison d'être is Foreman-style APIs. Ship a
runnable playbook (under `examples/foreman.yml`) that reproduces the
ancestry chain: organization → location → hostgroup → host.

---

## New features

### 18. Generalized fingerprint store — _large_

`MergeEngine.build()` already collects a `fingerprints` dict for every
backend; only the filesystem backend persists them (`_checksums.json`).
Generalize the persistence layer so `track_changes=true` works for
SQL / KV / HTTP too:

- Store `{identifier: fingerprint}` per role in a user-chosen location
  (`~/.cache/devitops.ansible/<role>.json` by default, overridable by
  `checksum_store: <path>`).
- On subsequent runs, diff the collected fingerprints against the
  stored ones; set `changed: true` and `changed_files`/`changed_keys`
  accordingly.

Unlocks safe-reload semantics for every remote store, not just the
filesystem.

### 19. `write` / `put` action mode — _large_

Add an opt-in `action: put` that round-trips the current merged config
back into the backend. Enables GitOps-style promotion: "whatever is in
`production/web/` is now authoritative; push it to SQL/KV."

Gate behind `check_mode` compatibility and require explicit
`overwrite: true` to avoid accidental writes.

### 20. Secrets-manager backends — _medium (each)_

Natural additions to the existing `ConfigBackend` Protocol:

- HashiCorp Vault (KV v2) — ancestry = key prefix, fingerprint =
  version metadata.
- AWS Secrets Manager / GCP Secret Manager / Azure Key Vault — each
  with its own native revision/etag.

Each is roughly a new `kv_*.py` adapter + factory.

### 21. Per-key merge strategy overrides — _medium_

Today the engine always deep-merges dicts and replaces lists. Ansible
itself supports `list_merge: replace | append | prepend` in
`group_vars`. Offer a similar hint either as:

- a side-file `<role>.merge.yaml` declaring per-key strategies, or
- a reserved meta key inside the config itself
  (`__merge__: {items: append}`).

### 22. `include_keys` / `exclude_keys` post-filter — _small_

After merging, allow restricting the returned dict to a set of top-level
keys (or paths like `database.host`). Keeps role-specific payloads
narrow without duplicating merge logic downstream.

### 23. Schema-driven type coercion — _medium_

Pair `validate_schema` with opt-in coercion: when schema says
`"type": "integer"` and merged value is `"5"`, coerce to `5`. Useful
when upstream stores are string-typed (env KVs, Consul, some REST APIs)
but consumers expect typed values.

### 24. Caching layer across Ansible plays — _medium_

`HTTPBackend` already caches within a single module invocation. A
cross-play on-disk cache (`~/.cache/devitops.ansible/http/`) keyed by
(URL, query params, ETag) would cut API calls dramatically when
multiple roles read from the same upstream. TTL + etag-based
revalidation.

### 25. Async / parallel layer fetching in `HTTPBackend` — _medium_

Layers are independent until merged; fetching them concurrently would
cut wall-clock latency on multi-layer APIs like Foreman. Use
`requests.Session` + `concurrent.futures.ThreadPoolExecutor` or migrate
to `httpx` with `asyncio` (bigger surface-area change).

### 26. First-class JSON-path / JMESPath data extraction — _small_

`HTTPLayer.data_path` is a dot-separated path. Supporting JMESPath
(`jmespath` dep is already common in Ansible contexts) would let layers
pick out nested-list items without requiring the `list_name_key`
transform.

### 27. Native check_mode support — _small_

The module currently declares `supports_check_mode=True`, but
`track_changes` is the only feature that touches state. Document which
parameters are check-mode-safe and which aren't; ideally make `dry_run`
redundant with check-mode.

---

## Packaging

### 28. Drop `pyproject.toml` → switch to `setup.cfg`? — _n/a_

Not recommended. The current `pyproject.toml` carries only pytest +
coverage config; it's fine.

### 29. Move integration subprocess tests into ansible-test sanity/network — _medium_

`ansible-test` is the blessed way to exercise Ansible modules. Map the
existing `tests/integration/` onto `tests/integration/targets/<name>/`
collection structure so `ansible-test integration --venv` runs them.

### 30. Ship example roles under `roles/` — _small_

A `roles/webapp_config/` role that wires `read_config` + `template` +
a schema file, plus the matching `site.yml`. Makes the README's "full
playbook example" copy-pasteable.

---

## Out of scope

- **YAML anchors / `!include` directives.** The merge model is already
  a hierarchy; mixing in YAML-level reuse breaks fingerprinting.
- **Jinja templating of merged values.** Ansible does this for every
  task already; re-templating inside `read_config` would be redundant
  and harder to reason about.
- **Write-through caches (Redis + DB).** Belongs in a dedicated
  cache-coherence layer, not in `read_config`.
