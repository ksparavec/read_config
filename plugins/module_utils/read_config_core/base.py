"""Backend-agnostic merging engine and backend protocol.

A ``ConfigBackend`` provides a hierarchical view of configuration data for a
given role. The engine knows nothing about files, databases, or APIs; it only
walks ancestry chains returned by the backend and merges data with Ansible's
``dict_merge``. Backends interpret locations however they want.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Protocol, runtime_checkable

from ansible.module_utils.common.dict_transformations import dict_merge

# A Location is opaque to the engine. Backends pick the representation that
# fits their storage model (absolute path, row id, URI, key prefix, etc.).
Location = str


@dataclass
class MergeResult:
    """Outcome of merging a single target's ancestry chain."""

    data: dict
    sources: list[str] = field(default_factory=list)
    fingerprints: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class ConfigBackend(Protocol):
    """Contract every storage backend must satisfy."""

    def discover(self, role_name: str) -> Iterable[Location]:
        """Return every location that holds config data for ``role_name``."""

    def resolve_ancestry(self, target: Location) -> list[Location]:
        """Return the ordered merge chain for ``target``.

        The chain must be inclusive of ``target`` and ordered lowest-precedence
        first (root → ... → target). A backend that considers ``target`` invalid
        (e.g. outside its root, unknown id) must raise ``ValueError``.
        """

    def load(self, location: Location, role_name: str) -> dict | None:
        """Return parsed data at ``location`` for ``role_name``, or ``None``."""

    def exists(self, location: Location, role_name: str) -> bool:
        """Return True if ``load`` would yield data. Used by dry_run paths."""

    def fingerprint(self, location: Location, role_name: str) -> str | None:
        """Return a change-detection token, or ``None`` if no data exists."""

    def identify(self, location: Location, role_name: str) -> str:
        """Return a stable, human-readable identifier for provenance output."""


class MergeEngine:
    """Walks a backend's ancestry chain and merges configs with dict_merge."""

    def __init__(self, backend: ConfigBackend) -> None:
        self._backend = backend

    @property
    def backend(self) -> ConfigBackend:
        return self._backend

    def discover(self, role_name: str) -> Iterable[Location]:
        return self._backend.discover(role_name)

    def build(
        self,
        target: Location,
        role_name: str,
        *,
        dry_run: bool = False,
    ) -> MergeResult:
        chain = self._backend.resolve_ancestry(target)
        merged: dict = {}
        sources: list[str] = []
        fingerprints: dict[str, str] = {}

        for location in chain:
            identifier = self._backend.identify(location, role_name)

            if dry_run:
                if not self._backend.exists(location, role_name):
                    continue
                sources.append(identifier)
                continue

            data = self._backend.load(location, role_name)
            if data is None:
                continue
            merged = dict_merge(merged, data)
            sources.append(identifier)
            fp = self._backend.fingerprint(location, role_name)
            if fp is not None:
                fingerprints[identifier] = fp

        return MergeResult(data=merged, sources=sources, fingerprints=fingerprints)
