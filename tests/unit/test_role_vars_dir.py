"""Unit tests for find_role_vars_dir."""
from __future__ import annotations

from pathlib import Path

import pytest


def _write_ansible_cfg(path: Path, roles_path: str) -> None:
    path.write_text(f"[defaults]\nroles_path = {roles_path}\n", encoding="utf-8")


def test_uses_ansible_config_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    roles_base = tmp_path / "roles"
    vars_dir = roles_base / "myrole" / "vars"
    vars_dir.mkdir(parents=True)
    cfg = tmp_path / "ansible.cfg"
    _write_ansible_cfg(cfg, str(roles_base))

    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))

    result = read_config_module.find_role_vars_dir("myrole")

    assert result == str(vars_dir)


def test_falls_back_to_ansible_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    ansible_home = tmp_path / "home"
    ansible_home.mkdir()
    roles_base = tmp_path / "roles"
    vars_dir = roles_base / "myrole" / "vars"
    vars_dir.mkdir(parents=True)
    _write_ansible_cfg(ansible_home / "ansible.cfg", str(roles_base))

    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    monkeypatch.setenv("ANSIBLE_HOME", str(ansible_home))

    result = read_config_module.find_role_vars_dir("myrole")

    assert result == str(vars_dir)


def test_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    home = tmp_path / "userhome"
    home.mkdir()
    roles_base = tmp_path / "roles"
    vars_dir = roles_base / "myrole" / "vars"
    vars_dir.mkdir(parents=True)
    _write_ansible_cfg(home / "ansible.cfg", str(roles_base))

    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    monkeypatch.delenv("ANSIBLE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(home))

    result = read_config_module.find_role_vars_dir("myrole")

    assert result == str(vars_dir)


def test_returns_none_when_no_config_reachable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    monkeypatch.delenv("ANSIBLE_CONFIG", raising=False)
    monkeypatch.delenv("ANSIBLE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "no-such-home"))

    assert read_config_module.find_role_vars_dir("anything") is None


def test_returns_none_when_config_points_to_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    monkeypatch.setenv("ANSIBLE_CONFIG", str(tmp_path / "missing.cfg"))
    monkeypatch.delenv("ANSIBLE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "no-home"))

    assert read_config_module.find_role_vars_dir("anything") is None


def test_returns_none_when_role_not_in_roles_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    roles_base = tmp_path / "roles"
    roles_base.mkdir()
    cfg = tmp_path / "ansible.cfg"
    _write_ansible_cfg(cfg, str(roles_base))

    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))

    assert read_config_module.find_role_vars_dir("ghost_role") is None


def test_searches_multiple_roles_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    vars_dir = second / "myrole" / "vars"
    vars_dir.mkdir(parents=True)

    cfg = tmp_path / "ansible.cfg"
    _write_ansible_cfg(cfg, f"{first}:{second}")

    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))

    assert read_config_module.find_role_vars_dir("myrole") == str(vars_dir)


def test_returns_none_on_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read_config_module
) -> None:
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("this is not an ini file\n" * 5 + "[[[bad", encoding="utf-8")

    monkeypatch.setenv("ANSIBLE_CONFIG", str(cfg))

    assert read_config_module.find_role_vars_dir("myrole") is None
