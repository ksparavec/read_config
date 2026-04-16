"""Unit tests targeting FilesystemBackend directly (protocol conformance)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from read_config_core.base import ConfigBackend, MergeEngine
from read_config_core.cache import ConfigCache
from read_config_core.filesystem import FilesystemBackend


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "fs"
    (root / "a" / "b").mkdir(parents=True)
    (root / "testrole.yaml").write_text("k1: base\n", encoding="utf-8")
    (root / "a" / "testrole.yaml").write_text("k2: from_a\n", encoding="utf-8")
    (root / "a" / "b" / "testrole.yaml").write_text("k3: from_b\n", encoding="utf-8")
    return root


def test_satisfies_protocol(tree: Path) -> None:
    assert isinstance(FilesystemBackend(str(tree)), ConfigBackend)


def test_discover_finds_every_matching_directory(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    assert set(backend.discover("testrole")) == {
        str(tree),
        str(tree / "a"),
        str(tree / "a" / "b"),
    }


def test_resolve_ancestry_returns_root_first_chain(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    chain = backend.resolve_ancestry(str(tree / "a" / "b"))

    assert chain == [str(tree), str(tree / "a"), str(tree / "a" / "b")]


def test_resolve_ancestry_for_root_is_singleton(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    assert backend.resolve_ancestry(str(tree)) == [str(tree)]


def test_resolve_ancestry_rejects_escape(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    with pytest.raises(ValueError, match="Path traversal"):
        backend.resolve_ancestry(str(tree.parent))


def test_load_returns_parsed_dict(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    assert backend.load(str(tree / "a"), "testrole") == {"k2": "from_a"}


def test_load_returns_none_when_no_match(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))
    (tree / "empty").mkdir()

    assert backend.load(str(tree / "empty"), "testrole") is None


def test_exists_matches_load_presence(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))
    (tree / "empty").mkdir()

    assert backend.exists(str(tree), "testrole") is True
    assert backend.exists(str(tree / "empty"), "testrole") is False


def test_fingerprint_is_stable_for_unchanged_file(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    first = backend.fingerprint(str(tree / "a"), "testrole")
    second = backend.fingerprint(str(tree / "a"), "testrole")

    assert first is not None
    assert first == second


def test_fingerprint_changes_when_file_changes(tree: Path) -> None:
    backend_a = FilesystemBackend(str(tree))
    first = backend_a.fingerprint(str(tree / "a"), "testrole")

    (tree / "a" / "testrole.yaml").write_text("k2: mutated\n", encoding="utf-8")
    backend_b = FilesystemBackend(str(tree))
    second = backend_b.fingerprint(str(tree / "a"), "testrole")

    assert first != second


def test_fingerprint_returns_none_when_absent(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))
    (tree / "empty").mkdir()

    assert backend.fingerprint(str(tree / "empty"), "testrole") is None


def test_identify_returns_file_path_when_present(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    assert backend.identify(str(tree / "a"), "testrole") == str(tree / "a" / "testrole.yaml")


def test_identify_falls_back_to_location_when_absent(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))
    (tree / "empty").mkdir()

    assert backend.identify(str(tree / "empty"), "testrole") == str(tree / "empty")


def test_engine_with_filesystem_backend_end_to_end(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))
    engine = MergeEngine(backend)

    result = engine.build(str(tree / "a" / "b"), "testrole")

    assert result.data == {"k1": "base", "k2": "from_a", "k3": "from_b"}
    assert result.sources == [
        str(tree / "testrole.yaml"),
        str(tree / "a" / "testrole.yaml"),
        str(tree / "a" / "b" / "testrole.yaml"),
    ]


def test_backend_defaults_create_fresh_cache(tree: Path) -> None:
    backend = FilesystemBackend(str(tree))

    assert isinstance(backend.cache, ConfigCache)
    assert backend.format_type == "yaml"


def test_backend_accepts_explicit_cache(tree: Path) -> None:
    cache = ConfigCache()
    backend = FilesystemBackend(str(tree), cache=cache)

    backend.load(str(tree), "testrole")

    # Cache is populated by the backend, proving it's actually shared.
    assert len(cache._checksums) == 1


def test_backend_supports_json_format(tmp_path: Path) -> None:
    root = tmp_path / "j"
    root.mkdir()
    (root / "testrole.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

    backend = FilesystemBackend(str(root), format_type="json")

    assert backend.load(str(root), "testrole") == {"x": 1}
    assert set(backend.discover("testrole")) == {str(root)}


def test_backend_supports_ini_format(tmp_path: Path) -> None:
    root = tmp_path / "i"
    root.mkdir()
    (root / "testrole.ini").write_text("[s]\nk = v\n", encoding="utf-8")

    backend = FilesystemBackend(str(root), format_type="ini")

    assert backend.load(str(root), "testrole") == {"s": {"k": "v"}}
