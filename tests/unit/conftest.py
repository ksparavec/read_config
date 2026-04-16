"""Shared pytest fixtures and Ansible module mocking helpers."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch

import pytest

# Ensure the module under test is importable without packaging.
_PLUGINS_MODULES = Path(__file__).resolve().parents[2] / "plugins" / "modules"
if str(_PLUGINS_MODULES) not in sys.path:
    sys.path.insert(0, str(_PLUGINS_MODULES))

from ansible.module_utils import basic  # noqa: E402
from ansible.module_utils.common.text.converters import to_bytes  # noqa: E402


# These mirror Ansible's real exit behavior (sys.exit -> SystemExit) by
# deriving from BaseException so the module's `except Exception` blocks don't
# swallow them.
class AnsibleExitJson(BaseException):
    """Raised by the patched exit_json to capture the module's success payload."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__(kwargs)
        self.kwargs = kwargs


class AnsibleFailJson(BaseException):
    """Raised by the patched fail_json to capture the module's failure payload."""

    def __init__(self, kwargs: dict[str, Any]) -> None:
        super().__init__(kwargs)
        self.kwargs = kwargs


def _exit_json(self: Any, **kwargs: Any) -> None:
    if "changed" not in kwargs:
        kwargs["changed"] = False
    raise AnsibleExitJson(kwargs)


def _fail_json(self: Any, **kwargs: Any) -> None:
    kwargs["failed"] = True
    raise AnsibleFailJson(kwargs)


def set_module_args(args: dict[str, Any]) -> None:
    """Inject module args so AnsibleModule reads them instead of stdin."""
    payload = json.dumps({"ANSIBLE_MODULE_ARGS": args})
    basic._ANSIBLE_ARGS = to_bytes(payload)
    # ansible-core >=2.19 requires a serialization profile; "legacy" matches module defaults.
    basic._ANSIBLE_PROFILE = "legacy"


@pytest.fixture
def patch_ansible_module() -> Iterator[None]:
    """Patch AnsibleModule.exit_json/fail_json to raise, so tests can assert results."""
    with patch.multiple(
        basic.AnsibleModule,
        exit_json=_exit_json,
        fail_json=_fail_json,
    ):
        yield


@pytest.fixture
def run_module_args(patch_ansible_module: None) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Return a callable that runs the module with given args and returns the result payload."""
    import read_config

    def _run(args: dict[str, Any]) -> dict[str, Any]:
        set_module_args(args)
        try:
            read_config.run_module()
        except AnsibleExitJson as exc:
            return {"ok": True, "result": exc.kwargs}
        except AnsibleFailJson as exc:
            return {"ok": False, "result": exc.kwargs}
        raise AssertionError(  # pragma: no cover - safety net
            "run_module did not call exit_json or fail_json"
        )

    return _run


@pytest.fixture
def tmp_role_tree(tmp_path: Path) -> Path:
    """Create a small role config tree with YAML files at multiple depths.

    Layout::

        <tmp>/config/
          testrole.yaml              -> {key1: base, key2: {subkey2a: base}}
          subfolder1/
            testrole.yaml            -> {key1: override1, config_tag: staging}
          subfolder2/
            testrole.yaml            -> {key2: {subkey2b: sub2b}, config_tag: production}
            subfolder3/
              testrole.yaml          -> {nested: deep}
          empty/                     (no matching config)
    """
    base = tmp_path / "config"
    (base / "subfolder1").mkdir(parents=True)
    (base / "subfolder2" / "subfolder3").mkdir(parents=True)
    (base / "empty").mkdir()

    (base / "testrole.yaml").write_text(
        "key1: base\nkey2:\n  subkey2a: base\n",
        encoding="utf-8",
    )
    (base / "subfolder1" / "testrole.yaml").write_text(
        "key1: override1\nconfig_tag: staging\n",
        encoding="utf-8",
    )
    (base / "subfolder2" / "testrole.yaml").write_text(
        "key2:\n  subkey2b: sub2b\nconfig_tag: production\n",
        encoding="utf-8",
    )
    (base / "subfolder2" / "subfolder3" / "testrole.yaml").write_text(
        "nested: deep\n",
        encoding="utf-8",
    )
    return base


@pytest.fixture
def tmp_json_tree(tmp_path: Path) -> Path:
    """Create a role config tree with JSON files."""
    base = tmp_path / "jsonconfig"
    (base / "sub").mkdir(parents=True)
    (base / "testrole.json").write_text(json.dumps({"key1": "base"}), encoding="utf-8")
    (base / "sub" / "testrole.json").write_text(json.dumps({"key2": "sub"}), encoding="utf-8")
    return base


@pytest.fixture
def tmp_ini_tree(tmp_path: Path) -> Path:
    """Create a role config tree with INI files."""
    base = tmp_path / "iniconfig"
    base.mkdir()
    (base / "testrole.ini").write_text(
        "[section1]\nkey1 = value1\n[section2]\nkey2 = value2\n",
        encoding="utf-8",
    )
    return base


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    """A JSON schema matching the tmp_role_tree base data shape."""
    schema = {
        "type": "object",
        "properties": {
            "key1": {"type": "string"},
            "key2": {"type": "object"},
            "config_tag": {"type": "string"},
        },
        "required": ["key1"],
        "additionalProperties": True,
    }
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def clear_ansible_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent ambient ANSIBLE_* env vars from leaking into tests."""
    for var in ("ANSIBLE_CONFIG", "ANSIBLE_HOME"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def read_config_module():
    """Import and return the module under test."""
    import read_config
    return read_config
