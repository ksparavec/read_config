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
    """Contract every storage backend must satisfy.

    Behavioral invariants (enforced by ``tests/unit/test_backend_contract.py``):

    * ``discover`` returns an iterable of location strings. Unknown roles yield
      an empty iterable, not an error.
    * ``resolve_ancestry(target)`` returns a non-empty list ending with
      ``target``. Invalid targets raise ``ValueError``.
    * ``exists(loc, role)`` is True iff ``load(loc, role)`` returns non-None.
    * ``fingerprint`` is stable across calls for unchanged data and ``None``
      when no data exists at the location.
    * ``identify`` returns a non-empty, stable string used for provenance
      (e.g. the ``files_merged`` list in the module output).
    """

    def discover(self, role_name: str) -> Iterable[Location]:
        """Return every location that holds config data for ``role_name``.

        Used by the module in multi-mode to enumerate all targets. Must be
        deterministic enough to produce consistent ``matched_count`` values
        across runs with unchanged data.
        """

    def resolve_ancestry(self, target: Location) -> list[Location]:
        """Return the ordered merge chain for ``target``.

        The chain is inclusive of ``target`` (as its last element) and ordered
        lowest-precedence first. For a filesystem layout, that means
        ``[root, .../intermediate, target]``. For other backends, "parent" is
        whatever the storage model defines (e.g. an SQL ``parent_id``,
        shortened key prefix).

        :raises ValueError: if ``target`` is outside the backend's purview
            (path traversal, unknown id, etc.).
        """

    def load(self, location: Location, role_name: str) -> dict | None:
        """Return parsed data at ``location`` for ``role_name``, or ``None``.

        ``None`` must be returned for locations that are part of a valid
        ancestry chain but simply have no config for this role (e.g. a parent
        directory without a matching file).
        """

    def exists(self, location: Location, role_name: str) -> bool:
        """Return True if ``load`` would yield data. Used by dry_run paths.

        Backends should make this cheaper than ``load`` where possible (e.g.
        ``os.path.isfile`` vs. parsing YAML, or an SQL EXISTS query vs. a
        full SELECT).
        """

    def fingerprint(self, location: Location, role_name: str) -> str | None:
        """Return a change-detection token, or ``None`` if no data exists.

        The token can be any string that changes when the underlying data
        changes — a content hash, a monotonic timestamp, an ETag. Two calls
        for unchanged data must return equal strings.
        """

    def identify(self, location: Location, role_name: str) -> str:
        """Return a stable, human-readable identifier for provenance output.

        For the filesystem backend this is the absolute config file path; for
        SQL it might be a row URI; for HTTP the fetched endpoint. The value
        appears verbatim in ``ansible_facts.read_config.configs[*].meta.files_merged``.
        """


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
