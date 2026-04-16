"""ConsulKVClient tests using a mock python-consul client."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from read_config_core.kv import KVClient
from read_config_core.kv_consul import ConsulKVClient, make_consul_backend


def test_consul_client_conforms_to_protocol() -> None:
    assert isinstance(ConsulKVClient(MagicMock()), KVClient)


def test_get_returns_value_bytes() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, {"Key": "myrole/production", "Value": b"payload", "ModifyIndex": 5})
    client = ConsulKVClient(consul)

    assert client.get("myrole/production") == b"payload"


def test_get_encodes_string_value() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, {"Value": "text"})
    client = ConsulKVClient(consul)

    assert client.get("k") == b"text"


def test_get_returns_none_for_missing_key() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, None)
    client = ConsulKVClient(consul)

    assert client.get("nope") is None


def test_get_returns_none_when_value_missing_from_payload() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, {"Key": "k"})  # no Value key
    client = ConsulKVClient(consul)

    assert client.get("k") is None


def test_keys_with_prefix_uses_recursive_get() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (
        1,
        [
            {"Key": "myrole/production", "Value": b"{}"},
            {"Key": "myrole/staging", "Value": b"{}"},
        ],
    )
    client = ConsulKVClient(consul)

    keys = list(client.keys_with_prefix("myrole/"))

    assert keys == ["myrole/production", "myrole/staging"]
    consul.kv.get.assert_called_once_with("myrole/", recurse=True)


def test_keys_with_prefix_empty_for_missing() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, None)
    client = ConsulKVClient(consul)

    assert list(client.keys_with_prefix("nothing/")) == []


def test_revision_uses_modify_index() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, {"Key": "k", "Value": b"v", "ModifyIndex": 42})
    client = ConsulKVClient(consul)

    assert client.revision("k") == "42"


def test_revision_none_when_key_missing() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, None)
    client = ConsulKVClient(consul)

    assert client.revision("k") is None


def test_revision_none_when_modify_index_absent() -> None:
    consul = MagicMock()
    consul.kv.get.return_value = (1, {"Key": "k", "Value": b"v"})  # no ModifyIndex
    client = ConsulKVClient(consul)

    assert client.revision("k") is None


def test_make_consul_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_consul = MagicMock()
    fake_consul.Consul = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "consul", fake_consul)

    backend = make_consul_backend(host="consul.example.com", port=8501, prefix="configs")

    assert backend.prefix == "configs"
    fake_consul.Consul.assert_called_once_with(host="consul.example.com", port=8501)
