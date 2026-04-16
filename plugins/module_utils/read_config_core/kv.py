"""Prefix-based KV store backend and the KVClient adapter protocol.

Storage model: every config is one key whose full name is
``{prefix}/{role_name}/{location}`` (separator configurable). Locations are
interpreted as path segments so ancestry = successive path prefixes of the
target. Data values are JSON.

KVClient is the thin adapter a concrete KV store must implement. It hides
vendor specifics (redis-py, etcd3, python-consul, etc.) from KVBackend so the
hierarchy + merging logic is written once.
"""
from __future__ import annotations

import hashlib
import json
from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class KVClient(Protocol):
    """Minimal KV-store adapter the KVBackend needs."""

    def get(self, key: str) -> bytes | None:
        """Return the raw bytes stored at ``key`` or ``None`` if absent."""

    def keys_with_prefix(self, prefix: str) -> Iterable[str]:
        """Yield every key that starts with ``prefix`` (string form)."""

    def revision(self, key: str) -> str | None:
        """Return an opaque change-detection token for ``key``.

        Native-versioning stores (etcd ``mod_revision``, Consul ``ModifyIndex``)
        return their revision as a string. Stores without native versioning
        (e.g. Redis) fall back to a content hash. ``None`` means absent.
        """


class InMemoryKVClient:
    """Dict-backed KVClient used for testing and local experimentation."""

    def __init__(self, initial: dict[str, bytes] | None = None) -> None:
        self._store: dict[str, bytes] = {}
        self._revisions: dict[str, int] = {}
        self._counter = 0
        for key, value in (initial or {}).items():
            self.set(key, value)

    def get(self, key: str) -> bytes | None:
        return self._store.get(key)

    def keys_with_prefix(self, prefix: str) -> Iterable[str]:
        return [k for k in self._store if k.startswith(prefix)]

    def revision(self, key: str) -> str | None:
        if key not in self._revisions:
            return None
        return str(self._revisions[key])

    # --- helpers for tests / direct usage -------------------------------
    def set(self, key: str, value: bytes) -> None:
        self._counter += 1
        self._store[key] = value
        self._revisions[key] = self._counter

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
        self._revisions.pop(key, None)


class KVBackend:
    """Hierarchical config from any KVClient-conforming store."""

    def __init__(
        self,
        client: KVClient,
        prefix: str = "",
        separator: str = "/",
    ) -> None:
        if not separator:
            raise ValueError("separator must be non-empty")
        self._client = client
        self._separator = separator
        # Allow the caller's prefix to end (or not) with the separator; we
        # normalize to "no trailing separator" internally.
        self._prefix = prefix.rstrip(separator)

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def separator(self) -> str:
        return self._separator

    @property
    def client(self) -> KVClient:
        return self._client

    def discover(self, role_name: str) -> Iterable[str]:
        role_prefix = self._role_prefix(role_name)
        for key in self._client.keys_with_prefix(role_prefix):
            location = key[len(role_prefix):]
            if location:
                yield location

    def resolve_ancestry(self, target: str) -> list[str]:
        if not target:
            return [""]
        parts = [p for p in target.split(self._separator) if p != ""]
        chain: list[str] = []
        current = ""
        for part in parts:
            current = f"{current}{self._separator}{part}" if current else part
            chain.append(current)
        return chain

    def load(self, location: str, role_name: str) -> dict | None:
        raw = self._client.get(self._full_key(role_name, location))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def exists(self, location: str, role_name: str) -> bool:
        return self._client.get(self._full_key(role_name, location)) is not None

    def fingerprint(self, location: str, role_name: str) -> str | None:
        key = self._full_key(role_name, location)
        rev = self._client.revision(key)
        if rev is not None:
            return rev
        # KVClient could not provide a revision; derive one from bytes if present.
        raw = self._client.get(key)
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def identify(self, location: str, role_name: str) -> str:
        return f"kv://{self._full_key(role_name, location)}"

    def _role_prefix(self, role_name: str) -> str:
        if self._prefix:
            return f"{self._prefix}{self._separator}{role_name}{self._separator}"
        return f"{role_name}{self._separator}"

    def _full_key(self, role_name: str, location: str) -> str:
        parts: list[str] = []
        if self._prefix:
            parts.append(self._prefix)
        parts.append(role_name)
        if location:
            parts.append(location)
        return self._separator.join(parts)
