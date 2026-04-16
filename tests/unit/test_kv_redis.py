"""RedisKVClient tests using fakeredis (pure-Python in-memory Redis)."""
from __future__ import annotations

import json

import pytest

fakeredis = pytest.importorskip("fakeredis")

from read_config_core.kv import KVBackend, KVClient
from read_config_core.kv_redis import RedisKVClient


def _pack(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


def test_redis_client_conforms_to_protocol(fake_redis) -> None:
    assert isinstance(RedisKVClient(fake_redis), KVClient)


def test_get_returns_raw_bytes(fake_redis) -> None:
    fake_redis.set("myrole/production", _pack({"k": "v"}))
    client = RedisKVClient(fake_redis)

    assert client.get("myrole/production") == _pack({"k": "v"})


def test_get_returns_none_when_absent(fake_redis) -> None:
    client = RedisKVClient(fake_redis)

    assert client.get("nope") is None


def test_keys_with_prefix_uses_scan(fake_redis) -> None:
    fake_redis.set("myrole/production", _pack({}))
    fake_redis.set("myrole/staging", _pack({}))
    fake_redis.set("other/production", _pack({}))
    client = RedisKVClient(fake_redis)

    keys = list(client.keys_with_prefix("myrole/"))

    assert set(keys) == {"myrole/production", "myrole/staging"}


def test_keys_returned_as_strings_not_bytes(fake_redis) -> None:
    fake_redis.set("myrole/production", _pack({}))
    client = RedisKVClient(fake_redis)

    for key in client.keys_with_prefix("myrole/"):
        assert isinstance(key, str)


def test_revision_is_none_so_backend_hashes(fake_redis) -> None:
    """Redis has no native versioning."""
    client = RedisKVClient(fake_redis)

    assert client.revision("anything") is None


def test_redis_backend_end_to_end(fake_redis) -> None:
    fake_redis.set("myrole/production", _pack({"k1": "base"}))
    fake_redis.set("myrole/production/web", _pack({"k2": "web"}))

    backend = KVBackend(RedisKVClient(fake_redis))

    assert backend.load("production", "myrole") == {"k1": "base"}
    assert set(backend.discover("myrole")) == {"production", "production/web"}


def test_redis_backend_fingerprint_uses_content_hash(fake_redis) -> None:
    fake_redis.set("myrole/production", _pack({"a": 1}))
    backend = KVBackend(RedisKVClient(fake_redis))

    first = backend.fingerprint("production", "myrole")

    fake_redis.set("myrole/production", _pack({"a": 2}))
    second = backend.fingerprint("production", "myrole")

    assert first is not None
    assert second is not None
    assert first != second
    assert len(first) == 64  # sha256 hex


def test_make_redis_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the factory: lazy-import redis, hand it a URL."""
    import sys
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from read_config_core import kv_redis

    captured: dict = {}

    def fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return MagicMock()  # stand-in for a redis.Redis instance

    monkeypatch.setitem(
        sys.modules, "redis", SimpleNamespace(from_url=fake_from_url)
    )

    backend = kv_redis.make_redis_backend(
        url="redis://example.com:6379/2", prefix="configs"
    )

    assert isinstance(backend, KVBackend)
    assert backend.prefix == "configs"
    assert captured["url"] == "redis://example.com:6379/2"
