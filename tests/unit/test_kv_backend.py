"""Unit tests for KVBackend using InMemoryKVClient."""
from __future__ import annotations

import hashlib
import json

import pytest

from read_config_core.base import MergeEngine
from read_config_core.kv import InMemoryKVClient, KVBackend, KVClient


def _pack(data: dict) -> bytes:
    return json.dumps(data).encode("utf-8")


def test_inmemory_client_conforms_to_protocol() -> None:
    assert isinstance(InMemoryKVClient(), KVClient)


def test_resolve_ancestry_path_segments() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert backend.resolve_ancestry("production/web/frontend") == [
        "production",
        "production/web",
        "production/web/frontend",
    ]


def test_resolve_ancestry_strips_empty_segments() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert backend.resolve_ancestry("/production//web/") == [
        "production",
        "production/web",
    ]


def test_resolve_ancestry_empty_target_returns_root() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert backend.resolve_ancestry("") == [""]


def test_resolve_ancestry_custom_separator() -> None:
    backend = KVBackend(InMemoryKVClient(), separator=".")

    assert backend.resolve_ancestry("prod.web.fe") == ["prod", "prod.web", "prod.web.fe"]


def test_empty_separator_rejected() -> None:
    with pytest.raises(ValueError, match="separator"):
        KVBackend(InMemoryKVClient(), separator="")


def test_load_returns_parsed_json() -> None:
    client = InMemoryKVClient(
        {"myrole/production": _pack({"k": "v"})}
    )
    backend = KVBackend(client)

    assert backend.load("production", "myrole") == {"k": "v"}


def test_load_none_for_missing_key() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert backend.load("production", "myrole") is None


def test_load_respects_prefix() -> None:
    client = InMemoryKVClient(
        {"configs/myrole/production": _pack({"k": "v"})}
    )
    backend = KVBackend(client, prefix="configs")

    assert backend.load("production", "myrole") == {"k": "v"}


def test_load_isolates_roles() -> None:
    client = InMemoryKVClient(
        {
            "myrole/production": _pack({"a": 1}),
            "other/production": _pack({"b": 2}),
        }
    )
    backend = KVBackend(client)

    assert backend.load("production", "myrole") == {"a": 1}
    assert backend.load("production", "other") == {"b": 2}


def test_exists_reflects_store_presence() -> None:
    client = InMemoryKVClient(
        {"myrole/production": _pack({"x": 1})}
    )
    backend = KVBackend(client)

    assert backend.exists("production", "myrole") is True
    assert backend.exists("staging", "myrole") is False


def test_discover_returns_all_locations_for_role() -> None:
    client = InMemoryKVClient(
        {
            "myrole/production": _pack({}),
            "myrole/production/web": _pack({}),
            "myrole/staging": _pack({}),
            "other/production": _pack({}),
        }
    )
    backend = KVBackend(client)

    assert set(backend.discover("myrole")) == {
        "production",
        "production/web",
        "staging",
    }


def test_discover_empty_for_unknown_role() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert list(backend.discover("ghost")) == []


def test_discover_with_prefix() -> None:
    client = InMemoryKVClient(
        {
            "configs/myrole/production": _pack({}),
            "configs/myrole/staging": _pack({}),
        }
    )
    backend = KVBackend(client, prefix="configs")

    assert set(backend.discover("myrole")) == {"production", "staging"}


def test_fingerprint_uses_native_revision_when_available() -> None:
    client = InMemoryKVClient({"myrole/production": _pack({"a": 1})})
    backend = KVBackend(client)

    first = backend.fingerprint("production", "myrole")

    client.set("myrole/production", _pack({"a": 2}))
    second = backend.fingerprint("production", "myrole")

    assert first is not None
    assert second is not None
    assert first != second


def test_fingerprint_none_when_key_absent() -> None:
    backend = KVBackend(InMemoryKVClient())

    assert backend.fingerprint("nope", "myrole") is None


def test_fingerprint_falls_back_to_content_hash_when_no_revision() -> None:
    """Backends without native versioning get a content-hash fingerprint."""
    value = _pack({"k": "v"})

    class NoRevClient:
        def get(self, key: str):
            return value if key == "myrole/production" else None

        def keys_with_prefix(self, prefix: str):
            return ["myrole/production"]

        def revision(self, key: str):
            return None

    backend = KVBackend(NoRevClient())
    fp = backend.fingerprint("production", "myrole")

    assert fp == hashlib.sha256(value).hexdigest()


def test_identify_is_stable_uri() -> None:
    client = InMemoryKVClient({"myrole/production": _pack({})})
    backend = KVBackend(client, prefix="configs")

    assert backend.identify("production", "myrole") == "kv://configs/myrole/production"


def test_end_to_end_merges_hierarchy() -> None:
    client = InMemoryKVClient(
        {
            "myrole/production": _pack({"k1": "base", "shared": "prod"}),
            "myrole/production/web": _pack({"k2": "web", "shared": "web"}),
            "myrole/production/web/fe": _pack({"k3": "fe"}),
        }
    )
    backend = KVBackend(client)
    engine = MergeEngine(backend)

    result = engine.build("production/web/fe", "myrole")

    assert result.data == {
        "k1": "base",
        "k2": "web",
        "k3": "fe",
        "shared": "web",
    }
    assert result.sources == [
        "kv://myrole/production",
        "kv://myrole/production/web",
        "kv://myrole/production/web/fe",
    ]


def test_properties_expose_configuration() -> None:
    client = InMemoryKVClient()
    backend = KVBackend(client, prefix="configs/", separator="/")

    assert backend.prefix == "configs"  # trailing separator stripped
    assert backend.separator == "/"
    assert backend.client is client
