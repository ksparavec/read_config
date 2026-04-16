"""etcd v3 adapter for the KVBackend.

Uses the ``mod_revision`` field etcd maintains on each key as the native
change-detection token.
"""
from __future__ import annotations

from typing import Any, Iterable

from .kv import KVBackend


class EtcdKVClient:
    """Adapt an ``etcd3`` client to the KVClient protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> bytes | None:
        value, _ = self._client.get(key)
        return value

    def keys_with_prefix(self, prefix: str) -> Iterable[str]:
        for _value, metadata in self._client.get_prefix(prefix):
            key = metadata.key
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    def revision(self, key: str) -> str | None:
        value, metadata = self._client.get(key)
        if value is None or metadata is None:
            return None
        return str(metadata.mod_revision)


def make_etcd_backend(
    host: str = "localhost",
    port: int = 2379,
    prefix: str = "",
    separator: str = "/",
    **etcd_kwargs: Any,
) -> KVBackend:
    """Factory: build a KVBackend backed by an ``etcd3`` client."""
    import etcd3  # lazy import

    client = etcd3.client(host=host, port=port, **etcd_kwargs)
    return KVBackend(EtcdKVClient(client), prefix=prefix, separator=separator)
