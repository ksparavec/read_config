"""Filesystem-backed implementation of the ConfigBackend protocol."""
from __future__ import annotations

import os
from typing import Iterable

from .cache import ConfigCache

_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "yaml": (".yaml", ".yml"),
    "json": (".json",),
    "ini": (".ini", ".cfg"),
}


def _extensions_for(format_type: str) -> tuple[str, ...]:
    return _EXTENSIONS.get(format_type, _EXTENSIONS["yaml"])


def get_config_file_if_exists(
    directory: str, role_name: str, format_type: str = "yaml"
) -> str | None:
    """If ``directory`` contains ``<role_name>.<ext>``, return its absolute path."""
    for ext in _extensions_for(format_type):
        candidate = os.path.join(directory, f"{role_name}{ext}")
        if os.path.isfile(candidate):
            return candidate
    return None


def find_directories_with_role_config(
    config_dir: str, role_name: str, format_type: str = "yaml"
) -> set[str]:
    """Walk ``config_dir`` and collect every directory holding a matching file."""
    wanted = {f"{role_name}{ext}" for ext in _extensions_for(format_type)}
    found: set[str] = set()
    for root, _, files in os.walk(config_dir):
        if any(f in wanted for f in files):
            found.add(root)
    return found


def validate_path_security(base_path: str, target_path: str) -> str:
    """Ensure ``target_path`` resolves inside ``base_path``. Returns abs target."""
    base = os.path.abspath(base_path)
    target = os.path.abspath(target_path)
    if not target.startswith(base + os.sep) and target != base:
        raise ValueError(f"Path traversal detected: {target} is outside {base}")
    return target


class FilesystemBackend:
    """Reads hierarchical config from a directory tree.

    Location semantics: a location is an absolute directory path inside ``root``.
    Ancestry = the chain from ``root`` down to the target, element by element.
    """

    def __init__(
        self,
        root: str,
        format_type: str = "yaml",
        cache: ConfigCache | None = None,
    ) -> None:
        self._root = os.path.abspath(root)
        self._format = format_type
        self._cache = cache if cache is not None else ConfigCache()

    @property
    def root(self) -> str:
        return self._root

    @property
    def format_type(self) -> str:
        return self._format

    @property
    def cache(self) -> ConfigCache:
        return self._cache

    def discover(self, role_name: str) -> Iterable[str]:
        return find_directories_with_role_config(self._root, role_name, self._format)

    def resolve_ancestry(self, target: str) -> list[str]:
        target_abs = validate_path_security(self._root, target)
        if target_abs == self._root:
            return [self._root]
        rel_parts = os.path.relpath(target_abs, self._root).split(os.sep)
        chain = [self._root]
        current = self._root
        for part in rel_parts:
            current = os.path.join(current, part)
            chain.append(current)
        return chain

    def load(self, location: str, role_name: str) -> dict | None:
        cfg = get_config_file_if_exists(location, role_name, self._format)
        if cfg is None:
            return None
        return self._cache.load_config(cfg, self._format)

    def exists(self, location: str, role_name: str) -> bool:
        return get_config_file_if_exists(location, role_name, self._format) is not None

    def fingerprint(self, location: str, role_name: str) -> str | None:
        cfg = get_config_file_if_exists(location, role_name, self._format)
        if cfg is None:
            return None
        # load_config populates _checksums as a side effect.
        self._cache.load_config(cfg, self._format)
        return self._cache._checksums.get(cfg)

    def identify(self, location: str, role_name: str) -> str:
        cfg = get_config_file_if_exists(location, role_name, self._format)
        return cfg if cfg is not None else location
