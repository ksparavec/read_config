"""Unit tests for validate_against_schema."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_validates_conforming_data(schema_file: Path, read_config_module) -> None:
    data = {"key1": "hello", "key2": {}, "config_tag": "production"}

    assert read_config_module.validate_against_schema(data, str(schema_file)) is True


def test_rejects_nonconforming_data(schema_file: Path, read_config_module) -> None:
    data = {"key2": {}}  # missing required key1

    with pytest.raises(ValueError, match="Schema validation failed"):
        read_config_module.validate_against_schema(data, str(schema_file))


def test_missing_schema_file_raises(tmp_path: Path, read_config_module) -> None:
    with pytest.raises(ValueError, match="Schema validation failed"):
        read_config_module.validate_against_schema({}, str(tmp_path / "absent.json"))


def test_invalid_schema_definition_raises(tmp_path: Path, read_config_module) -> None:
    bad_schema = tmp_path / "bad.json"
    bad_schema.write_text(json.dumps({"type": "not_a_real_type"}))

    with pytest.raises(ValueError, match="Schema validation failed"):
        read_config_module.validate_against_schema({}, str(bad_schema))
