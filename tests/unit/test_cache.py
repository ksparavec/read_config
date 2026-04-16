"""Unit tests for ConfigCache."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_loads_yaml(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("key: value\nnested:\n  n: 1\n")

    cache = read_config_module.ConfigCache()
    data = cache.load_config(str(cfg), "yaml")

    assert data == {"key": "value", "nested": {"n": 1}}


def test_loads_json(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.json"
    cfg.write_text(json.dumps({"a": 1}))

    cache = read_config_module.ConfigCache()
    data = cache.load_config(str(cfg), "json")

    assert data == {"a": 1}


def test_loads_ini(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.ini"
    cfg.write_text("[s1]\nk1 = v1\n[s2]\nk2 = v2\n")

    cache = read_config_module.ConfigCache()
    data = cache.load_config(str(cfg), "ini")

    assert data == {"s1": {"k1": "v1"}, "s2": {"k2": "v2"}}


def test_returns_copy_not_cached_reference(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("key: value\n")

    cache = read_config_module.ConfigCache()
    first = cache.load_config(str(cfg), "yaml")
    first["mutated"] = True
    second = cache.load_config(str(cfg), "yaml")

    assert "mutated" not in second


def test_caches_file_contents(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("key: original\n")

    cache = read_config_module.ConfigCache()
    first = cache.load_config(str(cfg), "yaml")
    cfg.write_text("key: changed\n")
    second = cache.load_config(str(cfg), "yaml")

    assert first == second == {"key": "original"}


def test_missing_file_raises_runtime_error(tmp_path: Path, read_config_module) -> None:
    cache = read_config_module.ConfigCache()

    with pytest.raises(RuntimeError, match="Error reading"):
        cache.load_config(str(tmp_path / "absent.yaml"), "yaml")


def test_invalid_yaml_raises_runtime_error(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("key: [unterminated\n")

    cache = read_config_module.ConfigCache()

    with pytest.raises(RuntimeError, match="Error reading"):
        cache.load_config(str(cfg), "yaml")


def test_empty_yaml_returns_empty_dict(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("")

    cache = read_config_module.ConfigCache()

    assert cache.load_config(str(cfg), "yaml") == {}


def test_checksum_recorded_on_load(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("key: value\n")

    cache = read_config_module.ConfigCache()
    cache.load_config(str(cfg), "yaml")

    assert str(cfg) in cache._checksums
    assert len(cache._checksums[str(cfg)]) == 64  # sha256 hex length


def test_load_previous_checksums_from_file(tmp_path: Path, read_config_module) -> None:
    checksum_path = tmp_path / "c.json"
    checksum_path.write_text(json.dumps({"a.yaml": "abc123"}))

    cache = read_config_module.ConfigCache()
    cache.load_previous_checksums(str(checksum_path))

    assert cache._previous_checksums == {"a.yaml": "abc123"}


def test_load_previous_checksums_missing_file_is_empty(
    tmp_path: Path, read_config_module
) -> None:
    cache = read_config_module.ConfigCache()
    cache.load_previous_checksums(str(tmp_path / "none.json"))

    assert cache._previous_checksums == {}


def test_save_and_reload_checksums_roundtrip(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("key: v\n")
    checksum_path = tmp_path / "sums.json"

    cache = read_config_module.ConfigCache()
    cache.load_config(str(cfg), "yaml")
    cache.save_checksums(str(checksum_path))

    loaded = json.loads(checksum_path.read_text())
    assert str(cfg) in loaded


def test_get_changed_files_detects_new_file(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("k: v\n")

    cache = read_config_module.ConfigCache()
    cache.load_config(str(cfg), "yaml")
    changed = cache.get_changed_files()

    assert str(cfg) in changed


def test_get_changed_files_detects_modified_file(
    tmp_path: Path, read_config_module
) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("k: v\n")
    checksum_path = tmp_path / "sums.json"

    first = read_config_module.ConfigCache()
    first.load_config(str(cfg), "yaml")
    first.save_checksums(str(checksum_path))

    cfg.write_text("k: changed\n")

    second = read_config_module.ConfigCache()
    second.load_previous_checksums(str(checksum_path))
    second.load_config(str(cfg), "yaml")

    assert str(cfg) in second.get_changed_files()


def test_get_changed_files_empty_when_unchanged(
    tmp_path: Path, read_config_module
) -> None:
    cfg = tmp_path / "a.yaml"
    cfg.write_text("k: v\n")
    checksum_path = tmp_path / "sums.json"

    first = read_config_module.ConfigCache()
    first.load_config(str(cfg), "yaml")
    first.save_checksums(str(checksum_path))

    second = read_config_module.ConfigCache()
    second.load_previous_checksums(str(checksum_path))
    second.load_config(str(cfg), "yaml")

    assert second.get_changed_files() == set()
