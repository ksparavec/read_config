"""End-to-end integration tests: run the module as a real Ansible subprocess."""
from __future__ import annotations

from pathlib import Path


def _read_config(payload: dict) -> dict:
    """Pull the read_config facts out of a module success payload."""
    return payload["ansible_facts"]["read_config"]


def test_multi_mode_merges_hierarchy(invoke_module, role_tree: Path) -> None:
    result = invoke_module({"role_name": "testrole", "config_dir": str(role_tree)})

    assert result.get("failed") is not True
    facts = _read_config(result)
    assert facts["mode"] == "multiple"
    assert facts["matched_count"] == 4

    # Deepest dir sees merged keys from every level along its path.
    deepest = facts["configs"][str(role_tree / "b" / "c")]
    assert deepest["data"] == {
        "k1": "base",
        "k2": {"a": "base", "b": "from_b"},
        "config_tag": "production",
        "nested": "deep",
    }
    assert deepest["meta"]["files_merged"] == [
        str(role_tree / "testrole.yaml"),
        str(role_tree / "b" / "testrole.yaml"),
        str(role_tree / "b" / "c" / "testrole.yaml"),
    ]


def test_single_mode_returns_one_entry(invoke_module, role_tree: Path) -> None:
    target = role_tree / "a"
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(role_tree),
            "config_path": str(target),
        }
    )

    assert result.get("failed") is not True
    facts = _read_config(result)
    assert facts["mode"] == "single"
    assert facts["matched_count"] == 1
    (entry,) = facts["configs"].values()
    assert entry["data"]["k1"] == "from_a"
    assert entry["data"]["k2"] == {"a": "base"}
    assert entry["data"]["config_tag"] == "staging"


def test_config_tag_filters_results(invoke_module, role_tree: Path) -> None:
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(role_tree),
            "config_tag": "production",
        }
    )

    facts = _read_config(result)
    # Only b and b/c inherit the production tag; a is staging, root has none.
    assert set(facts["configs"].keys()) == {
        str(role_tree / "b"),
        str(role_tree / "b" / "c"),
    }


def test_dry_run_lists_files_without_reading(invoke_module, role_tree: Path) -> None:
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(role_tree),
            "dry_run": True,
        }
    )

    facts = _read_config(result)
    for entry in facts["configs"].values():
        assert entry["data"] == {}
        assert len(entry["meta"]["files_merged"]) >= 1


def test_json_format(invoke_module, json_tree: Path) -> None:
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(json_tree),
            "format": "json",
        }
    )

    facts = _read_config(result)
    assert facts["matched_count"] == 2
    sub = facts["configs"][str(json_tree / "sub")]
    assert sub["data"] == {"k1": "base", "k2": "child"}


def test_ini_format(invoke_module, ini_tree: Path) -> None:
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(ini_tree),
            "format": "ini",
        }
    )

    facts = _read_config(result)
    (entry,) = facts["configs"].values()
    assert entry["data"] == {"s1": {"key": "value"}, "s2": {"other": "thing"}}


def test_schema_validation_accepts_conforming_data(
    invoke_module, role_tree: Path, schema_file: Path
) -> None:
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(role_tree),
            "validate_schema": str(schema_file),
        }
    )

    assert result.get("failed") is not True


def test_schema_validation_rejects_bad_data(
    invoke_module, tmp_path: Path, schema_file: Path
) -> None:
    bad = tmp_path / "bad"
    bad.mkdir()
    # Missing required "k1" key.
    (bad / "testrole.yaml").write_text("k2:\n  only: here\n", encoding="utf-8")

    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(bad),
            "validate_schema": str(schema_file),
        }
    )

    assert result.get("failed") is True
    assert "Schema validation failed" in result["msg"]


def test_track_changes_reports_first_run_and_persists_checksums(
    invoke_module, role_tree: Path
) -> None:
    args = {
        "role_name": "testrole",
        "config_dir": str(role_tree),
        "track_changes": True,
    }

    first = invoke_module(args)
    assert first["changed"] is True
    assert "changed_files" in _read_config(first)
    assert (role_tree / ".testrole_checksums.json").exists()

    second = invoke_module(args)
    assert second["changed"] is False
    assert "changed_files" not in _read_config(second)


def test_track_changes_detects_modifications(invoke_module, role_tree: Path) -> None:
    args = {
        "role_name": "testrole",
        "config_dir": str(role_tree),
        "track_changes": True,
    }
    invoke_module(args)  # seed checksums

    (role_tree / "testrole.yaml").write_text(
        "k1: mutated\nk2:\n  a: base\n", encoding="utf-8"
    )

    second = invoke_module(args)
    assert second["changed"] is True
    changed = _read_config(second)["changed_files"]
    assert str(role_tree / "testrole.yaml") in changed


def test_missing_config_dir_fails(invoke_module, tmp_path: Path) -> None:
    result = invoke_module(
        {"role_name": "testrole", "config_dir": str(tmp_path / "absent")}
    )

    assert result.get("failed") is True
    assert "does not exist" in result["msg"]


def test_role_name_with_separator_fails(invoke_module, role_tree: Path) -> None:
    result = invoke_module(
        {"role_name": "bad/role", "config_dir": str(role_tree)}
    )

    assert result.get("failed") is True
    assert "path separators" in result["msg"]


def test_empty_role_name_fails(invoke_module, role_tree: Path) -> None:
    # argument_spec treats "" as valid str, but the module validates non-empty.
    result = invoke_module(
        {"role_name": "   ", "config_dir": str(role_tree)}
    )

    assert result.get("failed") is True
    assert "role_name" in result["msg"]


def test_config_path_outside_config_dir_fails(
    invoke_module, role_tree: Path
) -> None:
    # role_tree.parent is outside role_tree — traversal attempt.
    result = invoke_module(
        {
            "role_name": "testrole",
            "config_dir": str(role_tree),
            "config_path": str(role_tree.parent),
        }
    )

    assert result.get("failed") is True
    assert "Path traversal" in result["msg"]


def test_unknown_role_returns_empty_matches(invoke_module, role_tree: Path) -> None:
    result = invoke_module(
        {"role_name": "ghost_role", "config_dir": str(role_tree)}
    )

    assert result.get("failed") is not True
    facts = _read_config(result)
    assert facts["matched_count"] == 0
    assert facts["configs"] == {}


def test_find_role_vars_dir_via_ansible_config(
    invoke_module, tmp_path: Path
) -> None:
    """End-to-end check of the ANSIBLE_CONFIG -> roles_path fallback path."""
    roles_root = tmp_path / "roles"
    role_vars = roles_root / "myrole" / "vars"
    role_vars.mkdir(parents=True)
    (role_vars / "myrole.yaml").write_text("discovered: true\n", encoding="utf-8")

    cfg = tmp_path / "ansible.cfg"
    cfg.write_text(f"[defaults]\nroles_path = {roles_root}\n", encoding="utf-8")

    # Passing env= scopes overrides to the subprocess; parent env is preserved.
    result = invoke_module(
        {"role_name": "myrole"},
        env={"ANSIBLE_CONFIG": str(cfg)},
    )

    assert result.get("failed") is not True
    facts = _read_config(result)
    assert facts["matched_count"] == 1
    (entry,) = facts["configs"].values()
    assert entry["data"] == {"discovered": True}
