"""Unit tests for MergeEngine — exercise engine behavior with a fake backend."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from read_config_core.base import ConfigBackend, MergeEngine, MergeResult


@dataclass
class FakeBackend:
    """In-memory backend used to isolate engine behavior from storage concerns.

    Locations are plain strings. Ancestry is supplied via ``chains`` (target →
    ordered list of locations, root-first). Data is supplied via ``data``.
    """

    data: dict[str, dict] = field(default_factory=dict)
    chains: dict[str, list[str]] = field(default_factory=dict)
    fingerprints: dict[str, str] = field(default_factory=dict)
    discovered: list[str] = field(default_factory=list)
    invalid_targets: set[str] = field(default_factory=set)

    # Track calls so tests can assert on interaction patterns.
    load_calls: list[str] = field(default_factory=list)

    def discover(self, role_name: str):
        return list(self.discovered)

    def resolve_ancestry(self, target: str) -> list[str]:
        if target in self.invalid_targets:
            raise ValueError(f"invalid target: {target}")
        return list(self.chains.get(target, [target]))

    def load(self, location: str, role_name: str):
        self.load_calls.append(location)
        return self.data.get(location)

    def exists(self, location: str, role_name: str) -> bool:
        return location in self.data

    def fingerprint(self, location: str, role_name: str):
        return self.fingerprints.get(location)

    def identify(self, location: str, role_name: str) -> str:
        return f"id::{location}"


def test_fake_backend_satisfies_protocol() -> None:
    assert isinstance(FakeBackend(), ConfigBackend)


def test_build_empty_chain_returns_empty_result() -> None:
    backend = FakeBackend(chains={"t": []})
    engine = MergeEngine(backend)

    result = engine.build("t", "r")

    assert result == MergeResult(data={}, sources=[], fingerprints={})


def test_build_merges_chain_in_order() -> None:
    backend = FakeBackend(
        chains={"child": ["root", "mid", "child"]},
        data={
            "root": {"a": 1, "shared": "root"},
            "mid": {"b": 2, "shared": "mid"},
            "child": {"c": 3, "shared": "child"},
        },
    )
    engine = MergeEngine(backend)

    result = engine.build("child", "role")

    # Child wins for overlapping keys.
    assert result.data == {"a": 1, "b": 2, "c": 3, "shared": "child"}
    assert result.sources == ["id::root", "id::mid", "id::child"]


def test_build_skips_locations_without_data() -> None:
    backend = FakeBackend(
        chains={"child": ["root", "missing", "child"]},
        data={"root": {"a": 1}, "child": {"b": 2}},
    )
    engine = MergeEngine(backend)

    result = engine.build("child", "role")

    assert result.data == {"a": 1, "b": 2}
    assert result.sources == ["id::root", "id::child"]


def test_build_records_fingerprints_for_loaded_locations() -> None:
    backend = FakeBackend(
        chains={"x": ["root", "x"]},
        data={"root": {"a": 1}, "x": {"b": 2}},
        fingerprints={"root": "sha-root", "x": "sha-x"},
    )
    engine = MergeEngine(backend)

    result = engine.build("x", "role")

    assert result.fingerprints == {"id::root": "sha-root", "id::x": "sha-x"}


def test_build_omits_fingerprint_when_backend_returns_none() -> None:
    backend = FakeBackend(
        chains={"x": ["x"]},
        data={"x": {"k": "v"}},
        fingerprints={},  # backend returns None
    )
    engine = MergeEngine(backend)

    result = engine.build("x", "role")

    assert result.fingerprints == {}


def test_dry_run_collects_sources_without_loading() -> None:
    backend = FakeBackend(
        chains={"t": ["root", "t"]},
        data={"root": {"a": 1}, "t": {"b": 2}},
    )
    engine = MergeEngine(backend)

    result = engine.build("t", "role", dry_run=True)

    assert result.data == {}
    assert result.sources == ["id::root", "id::t"]
    assert result.fingerprints == {}
    assert backend.load_calls == []  # load must not be invoked in dry_run


def test_dry_run_skips_missing_locations() -> None:
    backend = FakeBackend(
        chains={"t": ["root", "missing", "t"]},
        data={"root": {"a": 1}, "t": {"b": 2}},
    )
    engine = MergeEngine(backend)

    result = engine.build("t", "role", dry_run=True)

    assert result.sources == ["id::root", "id::t"]


def test_build_propagates_backend_validation_errors() -> None:
    backend = FakeBackend(invalid_targets={"/escape"})
    engine = MergeEngine(backend)

    with pytest.raises(ValueError, match="invalid target"):
        engine.build("/escape", "role")


def test_discover_delegates_to_backend() -> None:
    backend = FakeBackend(discovered=["a", "b", "c"])
    engine = MergeEngine(backend)

    assert list(engine.discover("role")) == ["a", "b", "c"]


def test_backend_property_exposes_underlying_backend() -> None:
    backend = FakeBackend()
    engine = MergeEngine(backend)

    assert engine.backend is backend
