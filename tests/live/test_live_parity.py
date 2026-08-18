"""Cross-backend parity: every live store must produce the same merge.

This is the suite's headline assertion. The same three-level hierarchy is
seeded into Postgres, MariaDB, Redis, etcd, Consul and a REST API, and each
one is merged through the same ``MergeEngine``. If the storage backend is
genuinely pluggable and the merge semantics genuinely are not, every backend
returns byte-identical data.
"""
from __future__ import annotations

import pytest

from read_config_core.base import MergeEngine

from .conftest import DEEPEST, EXPECTED_MERGE, HIERARCHY, ROLE

pytestmark = pytest.mark.live


def backend_load(case, location):
    """load() for a level, tolerating each backend's location convention."""
    return case.backend.load(location, ROLE)


def test_merge_is_identical_across_backends(parity_backend) -> None:
    name, backend, deepest = parity_backend

    result = MergeEngine(backend).build(deepest, ROLE)

    assert result.data == EXPECTED_MERGE, f"{name} produced a different merge"


def test_deep_merge_recurses_rather_than_replaces(parity_backend) -> None:
    """``database`` exists at all three levels; the result must be their union."""
    name, backend, deepest = parity_backend

    database = MergeEngine(backend).build(deepest, ROLE).data["database"]

    # pool_size overridden at every level, host set at the top and bottom.
    assert database == {"pool_size": 40, "host": "db.eu-west.internal"}, name


def test_scalar_precedence_is_child_wins(parity_backend) -> None:
    name, backend, deepest = parity_backend

    data = MergeEngine(backend).build(deepest, ROLE).data

    assert data["workers"] == 8, f"{name}: deepest level must win"
    assert data["log_level"] == "warn", f"{name}: middle level must beat root"
    assert data["listen_port"] == 8080, f"{name}: root-only key must survive"


def test_ancestry_chain_has_one_entry_per_level(parity_backend) -> None:
    name, backend, deepest = parity_backend

    chain = backend.resolve_ancestry(deepest)

    assert len(chain) == 3, f"{name}: expected 3 levels, got {chain}"
    assert chain[-1] == deepest, f"{name}: chain must end at the target"


def test_provenance_lists_every_contributing_source(parity_backend) -> None:
    name, backend, deepest = parity_backend

    sources = MergeEngine(backend).build(deepest, ROLE).sources

    assert len(sources) == 3, f"{name}: expected 3 sources, got {sources}"
    assert len(set(sources)) == 3, f"{name}: sources must be distinct"
    assert all(isinstance(s, str) and s for s in sources), name


def test_fingerprints_are_collected_for_every_source(parity_backend) -> None:
    name, backend, deepest = parity_backend

    result = MergeEngine(backend).build(deepest, ROLE)

    assert len(result.fingerprints) == 3, name
    assert all(v for v in result.fingerprints.values()), f"{name}: empty fingerprint"


def test_fingerprint_is_stable_across_reads(parity_backend) -> None:
    name, backend, deepest = parity_backend

    first = backend.fingerprint(deepest, ROLE)
    second = backend.fingerprint(deepest, ROLE)

    assert first == second, f"{name}: fingerprint changed without a data change"


def test_dry_run_lists_sources_without_merging(parity_backend) -> None:
    name, backend, deepest = parity_backend

    result = MergeEngine(backend).build(deepest, ROLE, dry_run=True)

    assert result.data == {}, f"{name}: dry_run must not merge"
    assert len(result.sources) == 3, f"{name}: dry_run must still report sources"


def test_intermediate_level_merges_only_its_ancestors(parity_backend) -> None:
    """Merging the middle level must not pull in the level below it."""
    name, backend, deepest = parity_backend
    middle = backend.resolve_ancestry(deepest)[1]

    data = MergeEngine(backend).build(middle, ROLE).data

    assert "region" not in data, f"{name}: deepest-level key leaked upward"
    assert data["workers"] == 8, name
    assert data["database"] == {"pool_size": 50, "host": "db.default.internal"}, name


def test_exists_agrees_with_load(parity_backend) -> None:
    name, backend, deepest = parity_backend

    for location in backend.resolve_ancestry(deepest):
        assert backend.exists(location, ROLE) is (
            backend.load(location, ROLE) is not None
        ), f"{name}: exists/load disagree at {location}"


def test_unknown_role_yields_nothing(parity_backend) -> None:
    name, backend, deepest = parity_backend
    if name == "api":
        # The role only scopes an HTTP layer when its url/params template
        # references {role_name}; these layers are deliberately role-agnostic.
        # See test_live_api.py::test_role_name_scopes_a_layer_only_when_templated.
        pytest.skip("api role scoping is URL-template driven")

    assert backend.load(deepest, "no-such-role") is None, name
    assert backend.exists(deepest, "no-such-role") is False, name
    assert backend.fingerprint(deepest, "no-such-role") is None, name


def test_each_level_round_trips_its_own_payload(parity_backend) -> None:
    """Each stored level reads back exactly as written, on every backend."""
    case = parity_backend

    for location, expected in zip(case.levels, HIERARCHY.values()):
        assert backend_load(case, location) == expected, f"{case.name} @ {location}"


def test_discover_finds_the_seeded_locations(parity_backend) -> None:
    case = parity_backend
    if case.name == "api":
        # ApiBackend.discover intentionally reports only the deepest
        # applicable layer, so multi-mode yields one fully merged result.
        assert list(case.backend.discover(ROLE)) == ["eu-west"]
        return

    discovered = set(case.backend.discover(ROLE))

    missing = set(case.levels) - discovered
    assert not missing, f"{case.name}: missing {missing}"


def test_root_key_is_not_an_implicit_ancestor(parity_backend) -> None:
    """SQL/KV chains start at the first path segment, not at an empty root.

    A config stored at the bare root key is never merged into descendants.
    Hierarchies must therefore be rooted at a named segment (here: "global").
    """
    name, backend, deepest = parity_backend
    if name in ("api", "filesystem"):
        pytest.skip(f"{name} ancestry is not a bare key path")

    chain = backend.resolve_ancestry(DEEPEST)

    assert "" not in chain, f"{name}: unexpected empty root in {chain}"
    assert chain[0] == "global", f"{name}: chain must start at the named root"
