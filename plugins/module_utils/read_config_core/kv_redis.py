"""Redis adapter for the KVBackend.

Redis has no native versioning, so ``revision`` falls back to a content hash
via KVBackend's default path.
"""
from __future__ import annotations

from typing import Any, Iterable

from .kv import KVBackend


class RedisKVClient:
    """Adapt a redis-py ``Redis`` client to the KVClient protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    def get(self, key: str) -> bytes | None:
        return self._client.get(key)

    def keys_with_prefix(self, prefix: str) -> Iterable[str]:
        pattern = f"{prefix}*"
        for key in self._client.scan_iter(match=pattern):
            yield key.decode("utf-8") if isinstance(key, bytes) else key

    def revision(self, key: str) -> str | None:
        # Redis doesn't version; KVBackend falls back to a content hash.
        return None


def make_redis_backend(
    url: str = "redis://localhost:6379/0",
    prefix: str = "",
    separator: str = "/",
    **redis_kwargs: Any,
) -> KVBackend:
    """Factory: build a KVBackend backed by a redis-py connection from ``url``."""
    import redis  # lazy import so filesystem-only users don't need redis-py

    client = redis.from_url(url, **redis_kwargs)
    return KVBackend(RedisKVClient(client), prefix=prefix, separator=separator)
