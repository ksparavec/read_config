"""Integration-style unit tests for run_module with a mocked AnsibleModule."""
from __future__ import annotations

import json
from pathlib import Path


def _facts(result: dict) -> dict:
    return result["result"]["ansible_facts"]["read_config"]


def test_empty_role_name_fails(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args({"role_name": "   ", "config_dir": str(tmp_role_tree)})

    assert result["ok"] is False
    assert "role_name" in result["result"]["msg"]


def test_role_name_with_slash_fails(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args({"role_name": "bad/role", "config_dir": str(tmp_role_tree)})

    assert result["ok"] is False
    assert "path separators" in result["result"]["msg"]


def test_missing_config_dir_fails(run_module_args, tmp_path: Path) -> None:
    result = run_module_args(
        {"role_name": "testrole", "config_dir": str(tmp_path / "nope")}
    )

    assert result["ok"] is False
    assert "does not exist" in result["result"]["msg"]


def test_config_dir_not_readable_fails(run_module_args, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        result = run_module_args({"role_name": "testrole", "config_dir": str(locked)})
    finally:
        locked.chmod(0o755)

    assert result["ok"] is False
    assert "not readable" in result["result"]["msg"]


def test_no_config_dir_and_no_ansible_config_fails(
    run_module_args, tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    monkeypatch.delenv("ANSIBLE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))

    result = run_module_args({"role_name": "testrole"})

    assert result["ok"] is False
    assert "Could not determine config_dir" in result["result"]["msg"]


def test_multiple_mode_returns_all_dirs(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args(
        {"role_name": "testrole", "config_dir": str(tmp_role_tree)}
    )

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["mode"] == "multiple"
    assert facts["matched_count"] == 4
    # Root directory's merged data should be just the base.
    root_entry = facts["configs"][str(tmp_role_tree)]
    assert root_entry["data"] == {"key1": "base", "key2": {"subkey2a": "base"}}


def test_empty_dir_returns_zero_matches(
    run_module_args, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = run_module_args({"role_name": "ghost", "config_dir": str(empty)})

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["mode"] == "multiple"
    assert facts["matched_count"] == 0
    assert facts["configs"] == {}


def test_single_mode_with_config_path(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "config_path": str(tmp_role_tree / "subfolder1"),
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["mode"] == "single"
    assert facts["matched_count"] == 1
    (entry,) = facts["configs"].values()
    assert entry["data"]["key1"] == "override1"
    assert entry["data"]["config_tag"] == "staging"


def test_single_mode_with_empty_dir_returns_zero(
    run_module_args, tmp_path: Path
) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    result = run_module_args(
        {
            "role_name": "ghost",
            "config_dir": str(empty),
            "config_path": str(empty),
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["mode"] == "single"
    assert facts["matched_count"] == 0


def test_config_tag_filters_multiple(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "config_tag": "production",
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    # Only subfolder2 and subfolder3 inherit production tag.
    paths = set(facts["configs"].keys())
    assert paths == {
        str(tmp_role_tree / "subfolder2"),
        str(tmp_role_tree / "subfolder2" / "subfolder3"),
    }


def test_config_tag_filters_single(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "config_path": str(tmp_role_tree / "subfolder1"),
            "config_tag": "production",
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["matched_count"] == 0
    assert facts["configs"] == {}


def test_dry_run_reports_files_without_reading(
    run_module_args, tmp_role_tree: Path
) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "dry_run": True,
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    for entry in facts["configs"].values():
        assert entry["data"] == {}
        assert len(entry["meta"]["files_merged"]) >= 1


def test_format_json_mode(run_module_args, tmp_json_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_json_tree),
            "format": "json",
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    assert facts["matched_count"] == 2


def test_format_ini_mode(run_module_args, tmp_ini_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_ini_tree),
            "format": "ini",
        }
    )

    assert result["ok"] is True
    facts = _facts(result)
    (entry,) = facts["configs"].values()
    assert entry["data"] == {"section1": {"key1": "value1"}, "section2": {"key2": "value2"}}


def test_schema_validation_success(
    run_module_args, tmp_role_tree: Path, schema_file: Path
) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "validate_schema": str(schema_file),
        }
    )

    assert result["ok"] is True


def test_schema_validation_failure(
    run_module_args, tmp_path: Path, schema_file: Path
) -> None:
    base = tmp_path / "bad"
    base.mkdir()
    (base / "testrole.yaml").write_text("key2:\n  only: here\n", encoding="utf-8")

    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(base),
            "validate_schema": str(schema_file),
        }
    )

    assert result["ok"] is False
    assert "Schema validation failed" in result["result"]["msg"]


def test_track_changes_first_run_marks_changed(
    run_module_args, tmp_role_tree: Path
) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "track_changes": True,
        }
    )

    assert result["ok"] is True
    assert result["result"]["changed"] is True
    facts = _facts(result)
    assert "changed_files" in facts
    assert len(facts["changed_files"]) >= 1

    checksum_file = tmp_role_tree / ".testrole_checksums.json"
    assert checksum_file.exists()


def test_track_changes_second_run_clean(
    run_module_args, tmp_role_tree: Path
) -> None:
    args = {
        "role_name": "testrole",
        "config_dir": str(tmp_role_tree),
        "track_changes": True,
    }
    first = run_module_args(args)
    assert first["ok"] is True

    second = run_module_args(args)
    assert second["ok"] is True
    assert second["result"]["changed"] is False
    assert "changed_files" not in _facts(second)


def test_track_changes_detects_modification(
    run_module_args, tmp_role_tree: Path
) -> None:
    args = {
        "role_name": "testrole",
        "config_dir": str(tmp_role_tree),
        "track_changes": True,
    }
    run_module_args(args)

    (tmp_role_tree / "testrole.yaml").write_text(
        "key1: mutated\nkey2:\n  subkey2a: base\n", encoding="utf-8"
    )

    second = run_module_args(args)
    assert second["ok"] is True
    assert second["result"]["changed"] is True
    changed = _facts(second)["changed_files"]
    assert str(tmp_role_tree / "testrole.yaml") in changed


def test_config_path_traversal_fails(run_module_args, tmp_role_tree: Path) -> None:
    result = run_module_args(
        {
            "role_name": "testrole",
            "config_dir": str(tmp_role_tree),
            "config_path": str(tmp_role_tree.parent),
        }
    )

    assert result["ok"] is False
    assert "Path traversal" in result["result"]["msg"]
