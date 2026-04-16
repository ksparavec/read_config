"""Consul KV adapter for the KVBackend.

Uses Consul's ``ModifyIndex`` as the native change-detection token.
"""
from __future__ import annotations

from typing import Any, Iterable

from .kv import KVBackend


class ConsulKVClient:
    """Adapt a ``python-consul`` client to the KVClient protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> bytes | None:
        _index, data = self._client.kv.get(key)
        if data is None:
            return None
        value = data.get("Value")
        if value is None:
            return None
        if isinstance(value, bytes):
            return value
        return value.encode("utf-8")

    def keys_with_prefix(self, prefix: str) -> Iterable[str]:
        _index, data = self._client.kv.get(prefix, recurse=True)
        if not data:
            return []
        return [item["Key"] for item in data]

    def revision(self, key: str) -> str | None:
        _index, data = self._client.kv.get(key)
        if data is None:
            return None
        modify_index = data.get("ModifyIndex")
        return None if modify_index is None else str(modify_index)


def make_consul_backend(
    host: str = "127.0.0.1",
    port: int = 8500,
    prefix: str = "",
    separator: str = "/",
    **consul_kwargs: Any,
) -> KVBackend:
    """Factory: build a KVBackend backed by a ``python-consul`` client."""
    import consul  # lazy import

    client = consul.Consul(host=host, port=port, **consul_kwargs)
    return KVBackend(ConsulKVClient(client), prefix=prefix, separator=separator)
