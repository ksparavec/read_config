"""Integration-test harness: invoke the module as a real Ansible script.

Each test spawns a subprocess running ``python plugins/modules/read_config.py
<args.json>``, which mirrors how ansible-core invokes modules on a target host.
This exercises the full ``main()`` path, real AnsibleModule initialization,
argument spec parsing, and JSON serialization.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "plugins" / "modules" / "read_config.py"


@pytest.fixture(scope="session")
def module_path() -> Path:
    """Absolute path to the module under test."""
    assert MODULE_PATH.is_file(), f"module not found at {MODULE_PATH}"
    return MODULE_PATH


@pytest.fixture(scope="session")
def python_executable() -> str:
    """Interpreter that has ansible-core + deps installed (this pytest's interpreter)."""
    return sys.executable


@pytest.fixture
def invoke_module(
    tmp_path: Path, python_executable: str, module_path: Path
) -> Callable[..., dict[str, Any]]:
    """Return a callable that invokes the module with the given args dict.

    Writes args to a JSON file (Ansible's module invocation protocol), runs the
    module as a subprocess, and parses stdout as JSON. Ansible modules always
    exit 0; success vs. failure is signaled by the ``failed`` key in the payload.
    """

    def _invoke(args: dict[str, Any], *, env: dict[str, str] | None = None) -> dict[str, Any]:
        args_file = tmp_path / "args.json"
        args_file.write_text(json.dumps({"ANSIBLE_MODULE_ARGS": args}), encoding="utf-8")

        proc = subprocess.run(
            [python_executable, str(module_path), str(args_file)],
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
            timeout=30,
            check=False,
        )
        # Ansible modules exit 0 on success and 1 on fail_json; both emit JSON on
        # stdout. Only a missing/unparseable payload signals a real crash.
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"module stdout was not valid JSON (exit={proc.returncode})\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            ) from exc

    return _invoke


@pytest.fixture
def role_tree(tmp_path: Path) -> Path:
    """Standard multi-level YAML config tree used across integration tests.

    Layout::

        <tmp>/cfg/
          testrole.yaml      {k1: base, k2: {a: base}}
          a/testrole.yaml    {k1: from_a, config_tag: staging}
          b/testrole.yaml    {k2: {b: from_b}, config_tag: production}
          b/c/testrole.yaml  {nested: deep}
          empty/
    """
    base = tmp_path / "cfg"
    (base / "a").mkdir(parents=True)
    (base / "b" / "c").mkdir(parents=True)
    (base / "empty").mkdir()

    (base / "testrole.yaml").write_text("k1: base\nk2:\n  a: base\n", encoding="utf-8")
    (base / "a" / "testrole.yaml").write_text(
        "k1: from_a\nconfig_tag: staging\n", encoding="utf-8"
    )
    (base / "b" / "testrole.yaml").write_text(
        "k2:\n  b: from_b\nconfig_tag: production\n", encoding="utf-8"
    )
    (base / "b" / "c" / "testrole.yaml").write_text("nested: deep\n", encoding="utf-8")
    return base


@pytest.fixture
def json_tree(tmp_path: Path) -> Path:
    base = tmp_path / "jcfg"
    (base / "sub").mkdir(parents=True)
    (base / "testrole.json").write_text(json.dumps({"k1": "base"}), encoding="utf-8")
    (base / "sub" / "testrole.json").write_text(
        json.dumps({"k2": "child"}), encoding="utf-8"
    )
    return base


@pytest.fixture
def ini_tree(tmp_path: Path) -> Path:
    base = tmp_path / "icfg"
    base.mkdir()
    (base / "testrole.ini").write_text(
        "[s1]\nkey = value\n[s2]\nother = thing\n", encoding="utf-8"
    )
    return base


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    schema = {
        "type": "object",
        "properties": {
            "k1": {"type": "string"},
            "k2": {"type": "object"},
            "config_tag": {"type": "string"},
        },
        "required": ["k1"],
        "additionalProperties": True,
    }
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(schema), encoding="utf-8")
    return path


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Auto-apply the integration marker to everything under tests/integration."""
    for item in items:
        if "tests/integration" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.integration)
