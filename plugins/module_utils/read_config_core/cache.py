"""In-memory config cache and checksum tracking used by FilesystemBackend."""
from __future__ import annotations

import configparser
import hashlib
import json

import yaml


class ConfigCache:
    """Cache parsed config files and track SHA-256 checksums of their bytes.

    Kept filesystem-specific for now (hashes raw file contents); other backends
    are free to provide their own fingerprinting strategy.
    """

    def __init__(self) -> None:
        self._cache: dict[str, dict] = {}
        self._checksums: dict[str, str] = {}
        self._previous_checksums: dict[str, str] = {}
        self._changed_files: set[str] = set()

    def load_config(self, filepath: str, format_type: str = "yaml") -> dict:
        """Load and cache a configuration file in the specified format."""
        if filepath not in self._cache:
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                if format_type == "yaml":
                    self._cache[filepath] = yaml.safe_load(content) or {}
                elif format_type == "json":
                    self._cache[filepath] = json.loads(content) or {}
                elif format_type == "ini":
                    parser = configparser.ConfigParser()
                    parser.read_string(content)
                    self._cache[filepath] = {
                        section: dict(parser.items(section))
                        for section in parser.sections()
                    }
                else:
                    raise ValueError(f"Unsupported format: {format_type}")
                self._checksums[filepath] = hashlib.sha256(content.encode()).hexdigest()
            except Exception as exc:
                raise RuntimeError(f"Error reading {filepath}: {exc}")
        return self._cache[filepath].copy()

    def load_previous_checksums(self, checksum_file: str) -> None:
        """Load previous checksums from file; absent file is treated as empty."""
        try:
            with open(checksum_file, "r") as f:
                self._previous_checksums = json.load(f)
        except FileNotFoundError:
            self._previous_checksums = {}

    def save_checksums(self, checksum_file: str) -> None:
        """Persist current checksums to file."""
        with open(checksum_file, "w") as f:
            json.dump(self._checksums, f)

    def get_changed_files(self) -> set[str]:
        """Return files whose checksum differs from the previous run."""
        self._changed_files = {
            path
            for path, checksum in self._checksums.items()
            if path not in self._previous_checksums
            or self._previous_checksums[path] != checksum
        }
        return self._changed_files
