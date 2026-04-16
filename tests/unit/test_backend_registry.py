"""Unit tests for the backend registry."""
from __future__ import annotations

from pathlib import Path

import pytest

from read_config_core import registry
from read_config_core.base import ConfigBackend
from read_config_core.filesystem import FilesystemBackend


def test_filesystem_is_registered_by_default() -> None:
    assert "filesystem" in registry.available_backends()


def test_get_backend_returns_filesystem_instance(tmp_path: Path) -> None:
    tmp_path.mkdir(exist_ok=True)
    backend = registry.get_backend("filesystem", root=str(tmp_path))

    assert isinstance(backend, FilesystemBackend)
    assert isinstance(backend, ConfigBackend)
    assert backend.root == str(tmp_path.resolve())


def test_get_backend_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="Unknown backend"):
        registry.get_backend("nope")


def test_register_backend_adds_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    # Snapshot + restore registry so one test can't pollute others.
    original = dict(registry._REGISTRY)
    monkeypatch.setattr(registry, "_REGISTRY", dict(original))

    class DummyBackend:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        def discover(self, role_name):  # pragma: no cover - protocol stub
            return []

        def resolve_ancestry(self, target):  # pragma: no cover
            return [target]

        def load(self, location, role_name):  # pragma: no cover
            return None

        def exists(self, location, role_name):  # pragma: no cover
            return False

        def fingerprint(self, location, role_name):  # pragma: no cover
            return None

        def identify(self, location, role_name):  # pragma: no cover
            return location

    registry.register_backend("dummy", DummyBackend)

    assert "dummy" in registry.available_backends()
    instance = registry.get_backend("dummy", foo="bar")
    assert isinstance(instance, DummyBackend)
    assert instance.kwargs == {"foo": "bar"}


def test_available_backends_is_sorted() -> None:
    names = registry.available_backends()

    assert names == sorted(names)
