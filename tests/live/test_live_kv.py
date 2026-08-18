"""KV backends against real Redis, etcd and Consul servers.

The three stores share ``KVBackend`` but differ in how they answer
``revision()``, which is what ``fingerprint()`` is built on:

* Redis has no native versioning  -> KVBackend falls back to a SHA-256 of the value.
* etcd exposes ``mod_revision``   -> used verbatim.
* Consul exposes ``ModifyIndex``  -> used verbatim.

These tests pin that difference down against the real servers, and prove the
fingerprint actually moves when the stored value is changed out from under the
backend.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from read_config_core.base import MergeEngine

from .conftest import (
    CONSUL_HOST,
    CONSUL_PORT,
    DEEPEST,
    ETCD_HOST,
    ETCD_PORT,
    EXPECTED_MERGE,
    HIERARCHY,
    REDIS_URL,
    ROLE,
)

pytestmark = pytest.mark.live

STORES = ("redis", "etcd", "consul")


@pytest.fixture(params=STORES)
def store(request, backend_redis, backend_etcd, backend_consul):
    return request.param, {
        "redis": backend_redis,
        "etcd": backend_etcd,
        "consul": backend_consul,
    }[request.param]


# --- shared KV behaviour ---------------------------------------------------

def test_merges_hierarchy(store) -> None:
    name, backend = store

    assert MergeEngine(backend).build(DEEPEST, ROLE).data == EXPECTED_MERGE, name


def test_key_layout_is_prefix_role_location(store) -> None:
    name, backend = store

    assert backend.identify(DEEPEST, ROLE) == f"kv://rclive/{ROLE}/{DEEPEST}", name


def test_discover_strips_the_role_prefix(store) -> None:
    name, backend = store

    discovered = set(backend.discover(ROLE))

    assert set(HIERARCHY) <= discovered, f"{name}: missing {set(HIERARCHY) - discovered}"
    assert all(not d.startswith("rclive") for d in discovered), name


def test_absent_key_reads_as_none(store) -> None:
    name, backend = store

    assert backend.load("global/nope", ROLE) is None, name
    assert backend.exists("global/nope", ROLE) is False, name
    assert backend.fingerprint("global/nope", ROLE) is None, name


# --- store-specific fingerprint sources ------------------------------------

def test_redis_fingerprint_is_a_content_hash(backend_redis) -> None:
    """Redis has no revision concept, so the value itself is hashed."""
    stored = json.dumps(HIERARCHY[DEEPEST]).encode()
    expected = hashlib.sha256(stored).hexdigest()

    assert backend_redis.fingerprint(DEEPEST, ROLE) == expected


def test_etcd_fingerprint_is_the_mod_revision(backend_etcd) -> None:
    etcd3 = pytest.importorskip("etcd3")
    client = etcd3.client(host=ETCD_HOST, port=ETCD_PORT)
    _value, meta = client.get(f"rclive/{ROLE}/{DEEPEST}")

    assert backend_etcd.fingerprint(DEEPEST, ROLE) == str(meta.mod_revision)


def test_consul_fingerprint_is_the_modify_index(backend_consul) -> None:
    consul = pytest.importorskip("consul")
    client = consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)
    _idx, data = client.kv.get(f"rclive/{ROLE}/{DEEPEST}")

    assert backend_consul.fingerprint(DEEPEST, ROLE) == str(data["ModifyIndex"])


# --- fingerprints must move when the data really moves ---------------------

def _rewrite(name: str, location: str, payload: dict) -> None:
    key = f"rclive/{ROLE}/{location}"
    body = json.dumps(payload)
    if name == "redis":
        redis = pytest.importorskip("redis")
        client = redis.from_url(REDIS_URL)
        client.set(key, body)
        client.close()
    elif name == "etcd":
        etcd3 = pytest.importorskip("etcd3")
        etcd3.client(host=ETCD_HOST, port=ETCD_PORT).put(key, body)
    else:
        consul = pytest.importorskip("consul")
        consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT).kv.put(key, body)


def test_fingerprint_changes_when_the_value_changes(store) -> None:
    name, backend = store
    before = backend.fingerprint(DEEPEST, ROLE)
    try:
        _rewrite(name, DEEPEST, dict(HIERARCHY[DEEPEST], workers=999))

        assert backend.fingerprint(DEEPEST, ROLE) != before, name
        assert MergeEngine(backend).build(DEEPEST, ROLE).data["workers"] == 999, name
    finally:
        _rewrite(name, DEEPEST, HIERARCHY[DEEPEST])

    assert MergeEngine(backend).build(DEEPEST, ROLE).data == EXPECTED_MERGE, name


def test_new_level_is_picked_up_without_reconstructing_the_backend(store) -> None:
    """Adding a deeper key extends the hierarchy on the next read."""
    name, backend = store
    extra = f"{DEEPEST}/rack7"
    try:
        _rewrite(name, extra, {"rack": 7, "database": {"pool_size": 1}})

        result = MergeEngine(backend).build(extra, ROLE)

        assert result.data["rack"] == 7, name
        assert result.data["listen_port"] == 8080, f"{name}: lost the root level"
        assert result.data["database"] == {
            "pool_size": 1,
            "host": "db.eu-west.internal",
        }, name
        assert len(result.sources) == 4, name
    finally:
        key = f"rclive/{ROLE}/{extra}"
        if name == "redis":
            redis = pytest.importorskip("redis")
            client = redis.from_url(REDIS_URL)
            client.delete(key)
            client.close()
        elif name == "etcd":
            etcd3 = pytest.importorskip("etcd3")
            etcd3.client(host=ETCD_HOST, port=ETCD_PORT).delete(key)
        else:
            consul = pytest.importorskip("consul")
            consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT).kv.delete(key)


def test_redis_url_carries_the_password(backend_redis) -> None:
    """The seeded server requires AUTH; a wrong password must fail."""
    redis = pytest.importorskip("redis")
    from read_config_core.kv_redis import make_redis_backend

    bad = make_redis_backend(url="redis://:wrongpass@127.0.0.1:16379/0", prefix="rclive")

    with pytest.raises(redis.exceptions.AuthenticationError):
        bad.load(DEEPEST, ROLE)
