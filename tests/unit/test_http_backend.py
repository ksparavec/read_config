"""Unit tests for HTTPBackend using requests-mock."""
from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace

import pytest

requests = pytest.importorskip("requests")
requests_mock = pytest.importorskip("requests_mock")

from read_config_core.base import MergeEngine
from read_config_core.http import HTTPBackend


BASE = "https://api.example.com/configs"


# --- URL construction ------------------------------------------------------
def test_url_for_single_segment_location() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.identify("production", "myrole") == f"{BASE}/myrole/production"


def test_url_for_nested_location_preserves_slashes() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert (
        backend.identify("production/web/frontend", "myrole")
        == f"{BASE}/myrole/production/web/frontend"
    )


def test_url_for_empty_location() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.identify("", "myrole") == f"{BASE}/myrole"


def test_url_url_encodes_role_name() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.identify("x", "role with spaces") == f"{BASE}/role%20with%20spaces/x"


def test_trailing_slash_on_base_url_is_normalized() -> None:
    backend = HTTPBackend(base_url=f"{BASE}/")

    assert backend.identify("production", "myrole") == f"{BASE}/myrole/production"


# --- resolve_ancestry -------------------------------------------------------
def test_resolve_ancestry_path_segments() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.resolve_ancestry("production/web/frontend") == [
        "production",
        "production/web",
        "production/web/frontend",
    ]


def test_resolve_ancestry_empty() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.resolve_ancestry("") == [""]


def test_resolve_ancestry_strips_empty_segments() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert backend.resolve_ancestry("//production//web/") == [
        "production",
        "production/web",
    ]


# --- load -------------------------------------------------------------------
def test_load_returns_parsed_json(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    backend = HTTPBackend(base_url=BASE)

    assert backend.load("production", "myrole") == {"k": "v"}


def test_load_returns_none_on_404(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", status_code=404)
    backend = HTTPBackend(base_url=BASE)

    assert backend.load("production", "myrole") is None


def test_load_raises_on_server_error(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", status_code=500)
    backend = HTTPBackend(base_url=BASE)

    with pytest.raises(requests.HTTPError):
        backend.load("production", "myrole")


def test_load_raises_on_client_error(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", status_code=401)
    backend = HTTPBackend(base_url=BASE)

    with pytest.raises(requests.HTTPError):
        backend.load("production", "myrole")


def test_load_returns_none_for_non_dict_payload(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json=["list", "not", "dict"])
    backend = HTTPBackend(base_url=BASE)

    assert backend.load("production", "myrole") is None


# --- data_path extraction ---------------------------------------------------
def test_data_path_extracts_nested_dict(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"result": {"data": {"k": "v"}}, "meta": {"tag": "x"}},
    )
    backend = HTTPBackend(base_url=BASE, data_path="result.data")

    assert backend.load("production", "myrole") == {"k": "v"}


def test_data_path_returns_none_when_unresolved(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"other": {}})
    backend = HTTPBackend(base_url=BASE, data_path="result.data")

    assert backend.load("production", "myrole") is None


def test_data_path_returns_none_when_leaf_not_dict(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"result": {"data": "string"}})
    backend = HTTPBackend(base_url=BASE, data_path="result.data")

    assert backend.load("production", "myrole") is None


# --- exists -----------------------------------------------------------------
def test_exists_true_for_200(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    backend = HTTPBackend(base_url=BASE)

    assert backend.exists("production", "myrole") is True


def test_exists_false_for_404(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", status_code=404)
    backend = HTTPBackend(base_url=BASE)

    assert backend.exists("production", "myrole") is False


def test_exists_matches_load_presence(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    requests_mock.get(f"{BASE}/myrole/staging", status_code=404)
    backend = HTTPBackend(base_url=BASE)

    for loc in ("production", "staging"):
        assert backend.exists(loc, "myrole") == (
            backend.load(loc, "myrole") is not None
        )


# --- fingerprint ------------------------------------------------------------
def test_fingerprint_uses_etag_when_present(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"k": "v"},
        headers={"ETag": '"abc123"'},
    )
    backend = HTTPBackend(base_url=BASE)

    assert backend.fingerprint("production", "myrole") == "abc123"


def test_fingerprint_strips_etag_quotes(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"k": "v"},
        headers={"ETag": '"weak-etag-value"'},
    )
    backend = HTTPBackend(base_url=BASE)

    fp = backend.fingerprint("production", "myrole")
    assert fp == "weak-etag-value"
    assert '"' not in fp


def test_fingerprint_falls_back_to_sha256_when_no_etag(requests_mock) -> None:
    body = json.dumps({"k": "v"})
    requests_mock.get(
        f"{BASE}/myrole/production",
        text=body,
        headers={"Content-Type": "application/json"},
    )
    backend = HTTPBackend(base_url=BASE)

    fp = backend.fingerprint("production", "myrole")
    assert fp == hashlib.sha256(body.encode("utf-8")).hexdigest()


def test_fingerprint_none_when_absent(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/staging", status_code=404)
    backend = HTTPBackend(base_url=BASE)

    assert backend.fingerprint("staging", "myrole") is None


def test_fingerprint_stable_for_unchanged_data(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"k": "v"},
        headers={"ETag": '"v1"'},
    )
    backend = HTTPBackend(base_url=BASE)

    assert backend.fingerprint("production", "myrole") == backend.fingerprint(
        "production", "myrole"
    )


# --- discover ---------------------------------------------------------------
def test_discover_returns_empty_without_discover_url() -> None:
    backend = HTTPBackend(base_url=BASE)

    assert list(backend.discover("myrole")) == []


def test_discover_returns_list_from_endpoint(requests_mock) -> None:
    discover = f"{BASE}/_discover/myrole"
    requests_mock.get(discover, json=["production", "staging"])
    backend = HTTPBackend(base_url=BASE, discover_url=discover)

    assert list(backend.discover("myrole")) == ["production", "staging"]


def test_discover_substitutes_role_placeholder(requests_mock) -> None:
    template = f"{BASE}/_discover/{{role}}/locations"
    requests_mock.get(
        f"{BASE}/_discover/myrole/locations", json=["production"]
    )
    backend = HTTPBackend(base_url=BASE, discover_url=template)

    assert list(backend.discover("myrole")) == ["production"]


def test_discover_url_encodes_role_placeholder(requests_mock) -> None:
    template = f"{BASE}/_discover/{{role}}"
    requests_mock.get(f"{BASE}/_discover/role%20x", json=["production"])
    backend = HTTPBackend(base_url=BASE, discover_url=template)

    assert list(backend.discover("role x")) == ["production"]


def test_discover_404_is_empty(requests_mock) -> None:
    discover = f"{BASE}/_discover/myrole"
    requests_mock.get(discover, status_code=404)
    backend = HTTPBackend(base_url=BASE, discover_url=discover)

    assert list(backend.discover("myrole")) == []


def test_discover_server_error_raises(requests_mock) -> None:
    discover = f"{BASE}/_discover/myrole"
    requests_mock.get(discover, status_code=500)
    backend = HTTPBackend(base_url=BASE, discover_url=discover)

    with pytest.raises(requests.HTTPError):
        list(backend.discover("myrole"))


def test_discover_rejects_non_list_response(requests_mock) -> None:
    discover = f"{BASE}/_discover/myrole"
    requests_mock.get(discover, json={"not": "a list"})
    backend = HTTPBackend(base_url=BASE, discover_url=discover)

    with pytest.raises(ValueError, match="JSON array"):
        list(backend.discover("myrole"))


def test_discover_rejects_non_string_elements(requests_mock) -> None:
    discover = f"{BASE}/_discover/myrole"
    requests_mock.get(discover, json=["ok", 42])
    backend = HTTPBackend(base_url=BASE, discover_url=discover)

    with pytest.raises(ValueError, match="non-string"):
        list(backend.discover("myrole"))


# --- auth / headers / timeout wiring ---------------------------------------
def test_headers_passed_to_requests(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    backend = HTTPBackend(
        base_url=BASE,
        headers={"Authorization": "Bearer xyz", "X-Custom": "1"},
    )

    backend.load("production", "myrole")

    last = requests_mock.last_request
    assert last.headers.get("Authorization") == "Bearer xyz"
    assert last.headers.get("X-Custom") == "1"


def test_basic_auth_passed_to_requests(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    backend = HTTPBackend(base_url=BASE, auth=("user", "pass"))

    backend.load("production", "myrole")

    last = requests_mock.last_request
    # requests encodes basic auth into the Authorization header.
    assert last.headers.get("Authorization", "").startswith("Basic ")


def test_auth_accepts_list_from_yaml(requests_mock) -> None:
    """YAML serializes tuples as lists; backend normalizes to tuple."""
    requests_mock.get(f"{BASE}/myrole/production", json={"k": "v"})
    backend = HTTPBackend(base_url=BASE, auth=["user", "pass"])

    backend.load("production", "myrole")

    assert requests_mock.last_request.headers.get("Authorization", "").startswith(
        "Basic "
    )


def test_timeout_passed_to_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            content=b"{}",
            headers={},
            json=lambda: {},
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(requests, "get", fake_get)
    backend = HTTPBackend(base_url=BASE, timeout=7.5)

    backend.load("production", "myrole")

    assert captured["timeout"] == 7.5


def test_verify_tls_passed_to_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status_code=200,
            content=b"{}",
            headers={},
            json=lambda: {},
            raise_for_status=lambda: None,
        )

    monkeypatch.setattr(requests, "get", fake_get)
    backend = HTTPBackend(base_url=BASE, verify_tls=False)

    backend.load("production", "myrole")

    assert captured["verify"] is False


# --- caching ---------------------------------------------------------------
def test_single_url_hits_endpoint_once_across_calls(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"k": "v"},
        headers={"ETag": '"v1"'},
    )
    backend = HTTPBackend(base_url=BASE)

    backend.load("production", "myrole")
    backend.exists("production", "myrole")
    backend.fingerprint("production", "myrole")

    assert requests_mock.call_count == 1


def test_cache_remembers_404(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/staging", status_code=404)
    backend = HTTPBackend(base_url=BASE)

    backend.exists("staging", "myrole")
    backend.load("staging", "myrole")
    backend.fingerprint("staging", "myrole")

    assert requests_mock.call_count == 1


# --- end-to-end via MergeEngine --------------------------------------------
def test_engine_end_to_end_merges_hierarchy(requests_mock) -> None:
    requests_mock.get(
        f"{BASE}/myrole/production",
        json={"k1": "base", "shared": "prod"},
    )
    requests_mock.get(
        f"{BASE}/myrole/production/web",
        json={"k2": "web", "shared": "web"},
    )
    requests_mock.get(
        f"{BASE}/myrole/production/web/frontend",
        json={"k3": "fe"},
    )

    backend = HTTPBackend(base_url=BASE)
    engine = MergeEngine(backend)

    result = engine.build("production/web/frontend", "myrole")

    assert result.data == {"k1": "base", "k2": "web", "k3": "fe", "shared": "web"}
    assert result.sources == [
        f"{BASE}/myrole/production",
        f"{BASE}/myrole/production/web",
        f"{BASE}/myrole/production/web/frontend",
    ]


def test_engine_skips_404_ancestors(requests_mock) -> None:
    requests_mock.get(f"{BASE}/myrole/production", json={"k1": "base"})
    requests_mock.get(f"{BASE}/myrole/production/web", status_code=404)
    requests_mock.get(
        f"{BASE}/myrole/production/web/fe", json={"k3": "fe"}
    )

    backend = HTTPBackend(base_url=BASE)
    engine = MergeEngine(backend)

    result = engine.build("production/web/fe", "myrole")

    assert result.data == {"k1": "base", "k3": "fe"}
    assert len(result.sources) == 2  # middle layer skipped


# --- validation ------------------------------------------------------------
def test_empty_base_url_rejected() -> None:
    with pytest.raises(ValueError, match="base_url"):
        HTTPBackend(base_url="")


# --- factory registration ---------------------------------------------------
def test_get_backend_returns_http_instance() -> None:
    from read_config_core.registry import get_backend

    backend = get_backend("http", base_url=BASE)

    assert isinstance(backend, HTTPBackend)
    assert backend.base_url == BASE


def test_http_backend_requires_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify the lazy-import error message when requests is not installed."""
    # Simulate requests missing by inserting a sentinel that raises on import
    original = sys.modules.get("requests")
    monkeypatch.setitem(sys.modules, "requests", None)
    # Force re-import by removing cached http module
    sys.modules.pop("read_config_core.http", None)
    try:
        from read_config_core.http import HTTPBackend as FreshHTTP

        with pytest.raises(ImportError, match="requests"):
            FreshHTTP(base_url=BASE)
    finally:
        # Restore
        if original is not None:
            sys.modules["requests"] = original
        sys.modules.pop("read_config_core.http", None)
