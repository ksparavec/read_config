# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build/Test Commands
- Primary test runner is pytest. Makefile targets wrap the common flows:
  - Unit tests: `make test` (≡ `pytest tests/unit`)
  - Integration (subprocess module invocation): `make integration`
  - Everything incl. live backends: `make test-all`
  - Terminal coverage: `make coverage`
  - HTML coverage: `make coverage-html` → `htmlcov/index.html`
- Run a single pytest test:
  `.venv/bin/pytest tests/unit/test_sql_backend.py::test_load_returns_data_for_known_location`
- Live backend tests (real Postgres/MariaDB/Redis/etcd/Consul/REST servers in
  Podman containers; containers are servers only, tests run on the host):
  - One-time deps: `make live-install`
  - Start the fleet: `make live-up` (parallel, ~10s, dedicated ports on 127.0.0.1)
  - Run: `make live` — or a single file:
    `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python .venv/bin/pytest tests/live/test_live_kv.py`
  - Inspect: `make live-status`, `make live-logs`, `make live-errors`
  - Tear down: `make live-down`
  - `make test-all` includes `tests/live`. Without the containers up those
    tests skip (the target prints a notice); `RC_LIVE_REQUIRE=1` makes the
    skip a hard failure. `make test` stays unit-only and needs no Podman.
  - `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` is mandatory for anything
    importing `etcd3` (its protobuf stubs predate protobuf 4). The `make live*`
    targets set it automatically.
- Legacy smoke playbook (not part of CI; requires the collection installed on
  `ANSIBLE_COLLECTIONS_PATH`):
  `ansible-playbook -i tests/inventory.ini tests/test_read_config_playbook.yml`
- Lint Python: `ruff check plugins/ tests/` — ruff is NOT in requirements-dev.txt;
  install it separately (`pip install ruff`) or run it via `uvx ruff`.
  There is no flake8, yamllint, or mypy config in this repo; do not assume
  those are available.
- Doc check: `ansible-doc -t module devitops.ansible.read_config` (requires the
  collection to be installed on `ANSIBLE_COLLECTIONS_PATH`; `make install-local`
  does that)

## Collection packaging
- `make build` → `devitops-ansible-<version>.tar.gz` (version comes from galaxy.yml)
- `make install-local` / `make uninstall-local` → `$COLLECTIONS_PATH`
  (default `~/.ansible/collections`)
- `make publish` → Ansible Galaxy; requires `GALAXY_API_TOKEN` in the environment
- `make clean` → removes `.venv`, caches, coverage, and build artifacts

## Code Style Guidelines
- **Python**: 4-space indentation, snake_case for functions/variables
- **Docstrings**: Use triple double-quotes (`"""`) with parameter descriptions
- **Imports**: Standard library first, then third-party (including Ansible)
- **Error handling**: Use Ansible's `module.fail_json()` for reporting errors
- **Ansible Documentation**: Maintain `DOCUMENTATION`, `EXAMPLES`, and `RETURN` sections
- **Variable naming**: Use descriptive names that indicate purpose
- **Module structure**: Follow Ansible collection structure conventions---

# Behavioral guidelines to reduce common LLM coding mistakes

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
