"""Unit tests for validate_path_security."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_subdirectory_is_allowed(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "base"
    target = base / "sub"
    base.mkdir()
    target.mkdir()

    result = read_config_module.validate_path_security(str(base), str(target))

    assert result == str(target.resolve())


def test_exact_match_is_allowed(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "base"
    base.mkdir()

    result = read_config_module.validate_path_security(str(base), str(base))

    assert result == str(base.resolve())


def test_parent_directory_is_rejected(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "base"
    base.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        read_config_module.validate_path_security(str(base), str(tmp_path))


def test_sibling_directory_is_rejected(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "base"
    sibling = tmp_path / "sibling"
    base.mkdir()
    sibling.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        read_config_module.validate_path_security(str(base), str(sibling))


def test_dotdot_traversal_is_rejected(tmp_path: Path, read_config_module) -> None:
    base = tmp_path / "base"
    base.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        read_config_module.validate_path_security(
            str(base), str(base / ".." / "escape")
        )


def test_prefix_overlap_is_rejected(tmp_path: Path, read_config_module) -> None:
    """A directory whose path starts with the base string but isn't a subdir must be rejected."""
    base = tmp_path / "base"
    confusable = tmp_path / "base-evil"
    base.mkdir()
    confusable.mkdir()

    with pytest.raises(ValueError, match="Path traversal detected"):
        read_config_module.validate_path_security(str(base), str(confusable))
