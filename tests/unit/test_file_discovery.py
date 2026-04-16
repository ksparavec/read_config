"""Unit tests for file discovery helpers."""
from __future__ import annotations

from pathlib import Path


def test_finds_yaml_with_yaml_extension(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.yaml"
    cfg.write_text("k: v\n")

    assert read_config_module.get_config_file_if_exists(str(tmp_path), "role") == str(cfg)


def test_finds_yaml_with_yml_extension(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.yml"
    cfg.write_text("k: v\n")

    assert read_config_module.get_config_file_if_exists(str(tmp_path), "role") == str(cfg)


def test_prefers_yaml_over_yml_when_both_exist(tmp_path: Path, read_config_module) -> None:
    yaml_file = tmp_path / "role.yaml"
    yml_file = tmp_path / "role.yml"
    yaml_file.write_text("a: 1\n")
    yml_file.write_text("a: 2\n")

    assert read_config_module.get_config_file_if_exists(str(tmp_path), "role") == str(yaml_file)


def test_returns_none_when_no_config(tmp_path: Path, read_config_module) -> None:
    assert read_config_module.get_config_file_if_exists(str(tmp_path), "missing") is None


def test_finds_json_when_format_json(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.json"
    cfg.write_text("{}")

    result = read_config_module.get_config_file_if_exists(
        str(tmp_path), "role", format_type="json"
    )

    assert result == str(cfg)


def test_finds_ini_when_format_ini(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.ini"
    cfg.write_text("[s]\nk=v\n")

    result = read_config_module.get_config_file_if_exists(
        str(tmp_path), "role", format_type="ini"
    )

    assert result == str(cfg)


def test_finds_cfg_fallback_for_ini_format(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.cfg"
    cfg.write_text("[s]\nk=v\n")

    result = read_config_module.get_config_file_if_exists(
        str(tmp_path), "role", format_type="ini"
    )

    assert result == str(cfg)


def test_unknown_format_falls_back_to_yaml(tmp_path: Path, read_config_module) -> None:
    cfg = tmp_path / "role.yaml"
    cfg.write_text("a: 1\n")

    result = read_config_module.get_config_file_if_exists(
        str(tmp_path), "role", format_type="weird"
    )

    assert result == str(cfg)


def test_find_directories_with_role_config_discovers_all(
    tmp_role_tree: Path, read_config_module
) -> None:
    dirs = read_config_module.find_directories_with_role_config(
        str(tmp_role_tree), "testrole"
    )

    expected = {
        str(tmp_role_tree),
        str(tmp_role_tree / "subfolder1"),
        str(tmp_role_tree / "subfolder2"),
        str(tmp_role_tree / "subfolder2" / "subfolder3"),
    }
    assert dirs == expected


def test_find_directories_excludes_unmatched_folders(
    tmp_role_tree: Path, read_config_module
) -> None:
    dirs = read_config_module.find_directories_with_role_config(
        str(tmp_role_tree), "testrole"
    )

    assert str(tmp_role_tree / "empty") not in dirs


def test_find_directories_returns_empty_when_role_missing(
    tmp_role_tree: Path, read_config_module
) -> None:
    dirs = read_config_module.find_directories_with_role_config(
        str(tmp_role_tree), "nonexistent"
    )

    assert dirs == set()


def test_find_directories_filters_by_format(
    tmp_json_tree: Path, read_config_module
) -> None:
    yaml_dirs = read_config_module.find_directories_with_role_config(
        str(tmp_json_tree), "testrole", format_type="yaml"
    )
    json_dirs = read_config_module.find_directories_with_role_config(
        str(tmp_json_tree), "testrole", format_type="json"
    )

    assert yaml_dirs == set()
    assert json_dirs == {str(tmp_json_tree), str(tmp_json_tree / "sub")}
