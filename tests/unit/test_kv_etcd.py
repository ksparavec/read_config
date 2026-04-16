"""EtcdKVClient tests using a mock etcd3 client.

We don't require the etcd3 library or a live etcd server for these tests —
they verify only that the adapter maps KVClient calls to etcd3's API shape.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from read_config_core.kv import KVClient
from read_config_core.kv_etcd import EtcdKVClient, make_etcd_backend


def test_etcd_client_conforms_to_protocol() -> None:
    assert isinstance(EtcdKVClient(MagicMock()), KVClient)


def test_get_returns_value_component() -> None:
    etcd = MagicMock()
    etcd.get.return_value = (b"payload", MagicMock(mod_revision=42))
    client = EtcdKVClient(etcd)

    assert client.get("myrole/production") == b"payload"
    etcd.get.assert_called_once_with("myrole/production")


def test_get_returns_none_when_value_is_none() -> None:
    etcd = MagicMock()
    etcd.get.return_value = (None, None)
    client = EtcdKVClient(etcd)

    assert client.get("nope") is None


def test_keys_with_prefix_iterates_get_prefix() -> None:
    etcd = MagicMock()
    etcd.get_prefix.return_value = [
        (b"v1", MagicMock(key=b"myrole/production")),
        (b"v2", MagicMock(key=b"myrole/staging")),
    ]
    client = EtcdKVClient(etcd)

    keys = list(client.keys_with_prefix("myrole/"))

    assert keys == ["myrole/production", "myrole/staging"]
    etcd.get_prefix.assert_called_once_with("myrole/")


def test_keys_handles_string_keys() -> None:
    etcd = MagicMock()
    etcd.get_prefix.return_value = [
        (b"v", MagicMock(key="myrole/production")),
    ]
    client = EtcdKVClient(etcd)

    assert list(client.keys_with_prefix("myrole/")) == ["myrole/production"]


def test_revision_returns_mod_revision_as_string() -> None:
    etcd = MagicMock()
    etcd.get.return_value = (b"payload", MagicMock(mod_revision=77))
    client = EtcdKVClient(etcd)

    assert client.revision("myrole/production") == "77"


def test_revision_none_when_key_missing() -> None:
    etcd = MagicMock()
    etcd.get.return_value = (None, None)
    client = EtcdKVClient(etcd)

    assert client.revision("nope") is None


def test_make_etcd_backend_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Factory should lazy-import etcd3 and wire host/port through."""
    fake_etcd = MagicMock()
    fake_etcd.client = MagicMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "etcd3", fake_etcd)

    backend = make_etcd_backend(host="etcd.example.com", port=2380, prefix="configs")

    assert backend.prefix == "configs"
    fake_etcd.client.assert_called_once_with(host="etcd.example.com", port=2380)
