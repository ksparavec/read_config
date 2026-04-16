"""Unit tests for build_merged_config_for_directory."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_merges_base_only(tmp_role_tree: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(tmp_role_tree),
        config_dir=str(tmp_role_tree),
        role_name="testrole",
        config_cache=cache,
    )

    assert data == {"key1": "base", "key2": {"subkey2a": "base"}}
    assert files == [str(tmp_role_tree / "testrole.yaml")]


def test_merges_parent_and_child(tmp_role_tree: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()
    target = tmp_role_tree / "subfolder1"

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(target),
        config_dir=str(tmp_role_tree),
        role_name="testrole",
        config_cache=cache,
    )

    # Child key1 overrides parent; parent key2 preserved.
    assert data["key1"] == "override1"
    assert data["key2"] == {"subkey2a": "base"}
    assert data["config_tag"] == "staging"
    assert files == [
        str(tmp_role_tree / "testrole.yaml"),
        str(target / "testrole.yaml"),
    ]


def test_merges_nested_tree(tmp_role_tree: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()
    target = tmp_role_tree / "subfolder2" / "subfolder3"

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(target),
        config_dir=str(tmp_role_tree),
        role_name="testrole",
        config_cache=cache,
    )

    assert data["key1"] == "base"
    assert data["key2"] == {"subkey2a": "base", "subkey2b": "sub2b"}
    assert data["config_tag"] == "production"
    assert data["nested"] == "deep"
    assert len(files) == 3


def test_dry_run_does_not_read_files(tmp_role_tree: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(tmp_role_tree / "subfolder1"),
        config_dir=str(tmp_role_tree),
        role_name="testrole",
        config_cache=cache,
        dry_run=True,
    )

    assert data == {}
    assert len(files) == 2
    # Nothing should have been loaded into the cache.
    assert cache._cache == {}


def test_empty_intermediate_dir_is_skipped(tmp_role_tree: Path, read_config_module) -> None:
    # subfolder2 -> subfolder3 path contains a config at each level; use a
    # target with an empty parent to confirm skipping silent parents works.
    cache = read_config_module.ConfigCache()
    deep = tmp_role_tree / "empty" / "nested"
    deep.mkdir(parents=True)
    (deep / "testrole.yaml").write_text("only: deep\n", encoding="utf-8")

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(deep),
        config_dir=str(tmp_role_tree),
        role_name="testrole",
        config_cache=cache,
    )

    # Only base and deep have matching files.
    assert data == {
        "key1": "base",
        "key2": {"subkey2a": "base"},
        "only": "deep",
    }
    assert files == [
        str(tmp_role_tree / "testrole.yaml"),
        str(deep / "testrole.yaml"),
    ]


def test_target_outside_config_dir_raises(tmp_role_tree: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()

    with pytest.raises(RuntimeError, match="Path traversal"):
        read_config_module.build_merged_config_for_directory(
            target_dir=str(tmp_role_tree.parent),
            config_dir=str(tmp_role_tree),
            role_name="testrole",
            config_cache=cache,
        )


def test_no_matching_files_returns_empty(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "cfg"
    base.mkdir()
    cache = read_config_module.ConfigCache()

    data, files = read_config_module.build_merged_config_for_directory(
        target_dir=str(base),
        config_dir=str(base),
        role_name="testrole",
        config_cache=cache,
    )

    assert data == {}
    assert files == []
