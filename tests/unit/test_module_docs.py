"""The module's embedded documentation must stay machine-readable.

``ansible-doc`` and ``ansible-test sanity --test validate-modules`` parse the
DOCUMENTATION / EXAMPLES / RETURN strings as YAML. A stray colon inside a
C(...) token silently breaks that, which has happened twice in this repo's
history, so it is pinned here rather than left to a manual doc check.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

BLOCKS = ("DOCUMENTATION", "EXAMPLES", "RETURN")


@pytest.fixture
def source(read_config_module) -> str:
    return Path(read_config_module.__file__).read_text(encoding="utf-8")


def extract(source: str, name: str) -> str:
    match = re.search(name + r" = r'''(.*?)'''", source, re.S)
    assert match, f"{name} block not found"
    return match.group(1)


@pytest.mark.parametrize("name", BLOCKS)
def test_doc_block_is_valid_yaml(source: str, name: str) -> None:
    assert yaml.safe_load(extract(source, name)) is not None


@pytest.mark.parametrize("name", BLOCKS)
def test_no_colon_inside_a_c_token(source: str, name: str) -> None:
    """C(key: value) parses as a YAML mapping and breaks the block."""
    offenders = re.findall(r"C\([^)]*:[^)]*\)", extract(source, name))

    assert not offenders, f"{name} has YAML-breaking C() tokens: {offenders}"


def argument_spec_names(source: str) -> set[str]:
    """Option names from the module_args literal (AST, not a regex).

    Several entries span multiple lines, so a line-oriented pattern misses
    them and would make this test pass vacuously.
    """
    import ast

    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "module_args" for t in node.targets
        ):
            return {kw.arg for kw in node.value.keywords}
    raise AssertionError("module_args assignment not found")


def test_every_documented_option_exists_in_the_argument_spec(source: str) -> None:
    documented = set(yaml.safe_load(extract(source, "DOCUMENTATION"))["options"])
    spec = argument_spec_names(source)

    assert documented == spec, (
        f"documented but not in spec: {sorted(documented - spec)}; "
        f"in spec but undocumented: {sorted(spec - documented)}"
    )


def test_examples_use_the_fully_qualified_collection_name(source: str) -> None:
    tasks = yaml.safe_load(extract(source, "EXAMPLES"))

    assert all("devitops.ansible.read_config" in task for task in tasks)


def test_examples_cover_every_backend(source: str) -> None:
    tasks = yaml.safe_load(extract(source, "EXAMPLES"))
    used = {
        task["devitops.ansible.read_config"].get("backend", "filesystem")
        for task in tasks
    }

    assert used == {"filesystem", "sql", "redis", "etcd", "consul", "api"}
