"""Unit tests for the layered ApiBackend using requests-mock."""
from __future__ import annotations

import hashlib
import json
import sys
from types import SimpleNamespace
from typing import Any

import pytest

requests = pytest.importorskip("requests")
requests_mock_pkg = pytest.importorskip("requests_mock")

from read_config_core.base import MergeEngine
from read_config_core.api import ApiBackend, ApiLayer


def _layer(name: str, url: str, **kwargs: Any) -> dict:
    base = {"name": name, "url": url}
    base.update(kwargs)
    return base


# --- construction & validation ---------------------------------------------
def test_empty_layers_rejected() -> None:
    with pytest.raises(ValueError, match="either 'layers' or an 'api' preset"):
        ApiBackend(layers=[])


def test_duplicate_layer_names_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate layer"):
        ApiBackend(
            layers=[
                _layer("host", "https://example.com/a"),
                _layer("host", "https://example.com/b"),
            ]
        )


def test_layer_without_url_rejected() -> None:
    with pytest.raises(ValueError, match="'url' is required"):
        ApiBackend(layers=[{"name": "host"}])


def test_non_dict_layer_rejected() -> None:
    with pytest.raises(TypeError, match="expected dict or ApiLayer"):
        ApiBackend(layers=["not-a-dict"])


def test_layer_accepts_apilayer_instance() -> None:
    layer = ApiLayer(name="host", url="https://example.com/x")
    backend = ApiBackend(layers=[layer])

    assert backend.layers[0] is layer


def test_layer_indexes_default_to_stringified_position() -> None:
    backend = ApiBackend(layers=[{"url": "https://example.com/x"}])

    assert backend.layers[0].name == "0"


# --- resolve_ancestry + discovery ------------------------------------------
def test_resolve_ancestry_single_layer() -> None:
    backend = ApiBackend(layers=[_layer("only", "https://example.com/x")])

    assert backend.resolve_ancestry("only") == ["only"]


def test_resolve_ancestry_multi_layer_truncates_at_target() -> None:
    backend = ApiBackend(
        layers=[
            _layer("org", "https://example.com/o"),
            _layer("domain", "https://example.com/d"),
            _layer("host", "https://example.com/h"),
        ]
    )

    assert backend.resolve_ancestry("domain") == ["org", "domain"]


def test_resolve_ancestry_filters_by_excludes() -> None:
    backend = ApiBackend(
        layers=[
            _layer("org", "https://example.com/o"),
            _layer("host", "https://example.com/h"),
        ],
        excludes=["org"],
    )

    # 'org' requires organization_id which is absent, so it drops out.
    assert backend.resolve_ancestry("host") == ["host"]


def test_resolve_ancestry_raises_for_unknown_or_excluded_layer() -> None:
    backend = ApiBackend(
        layers=[
            _layer("org", "https://example.com/o"),
            _layer("host", "https://example.com/h"),
        ],
        excludes=["org"],
    )

    with pytest.raises(ValueError, match="Unknown layer"):
        backend.resolve_ancestry("org")  # configured but not applicable


def test_discover_returns_deepest_applicable_layer() -> None:
    backend = ApiBackend(
        layers=[
            _layer("org", "https://example.com/o"),
            _layer("host", "https://example.com/h"),
        ]
    )

    assert list(backend.discover("myrole")) == ["host"]


def test_excluding_every_layer_is_rejected() -> None:
    with pytest.raises(ValueError, match="removes every layer"):
        ApiBackend(
            layers=[_layer("host", "https://example.com/h")],
            excludes=["host"],
        )


# --- load + placeholders ----------------------------------------------------
def test_load_substitutes_context_placeholders(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/organizations/3/parameters",
        json={"k": "v"},
    )
    backend = ApiBackend(
        layers=[_layer("org", "https://example.com/organizations/{organization_id}/parameters")],
        organization_id=3,
    )

    assert backend.load("org", "myrole") == {"k": "v"}


def test_load_substitutes_role_name(requests_mock) -> None:
    requests_mock.get("https://example.com/myrole/params", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("r", "https://example.com/{role_name}/params")]
    )

    assert backend.load("r", "myrole") == {"k": "v"}


def test_load_substitutes_location_as_layer_name(requests_mock) -> None:
    requests_mock.get("https://example.com/endpoint/host", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("host", "https://example.com/endpoint/{location}")]
    )

    assert backend.load("host", "myrole") == {"k": "v"}


def test_load_substitutes_in_query_params(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/params",
        json={"k": "v"},
    )
    backend = ApiBackend(
        layers=[
            _layer(
                "host",
                "https://example.com/params",
                params={"role": "{role_name}", "host_id": "{host_id}"},
            )
        ],
        host_id="42",
    )

    backend.load("host", "myrole")

    last = requests_mock.last_request
    assert last.qs == {"role": ["myrole"], "host_id": ["42"]}


def test_load_substitutes_in_headers(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[
            _layer(
                "host",
                "https://example.com/x",
                headers={"X-Role": "{role_name}"},
            )
        ]
    )

    backend.load("host", "myrole")

    assert requests_mock.last_request.headers.get("X-Role") == "myrole"


def test_unknown_placeholder_raises(requests_mock) -> None:
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/{undefined_key}")]
    )

    with pytest.raises(ValueError, match="undefined_key"):
        backend.load("x", "myrole")


# --- 404 + error handling ---------------------------------------------------
def test_load_returns_none_on_404(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=404)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert backend.load("x", "myrole") is None


def test_load_raises_on_500(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=500)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    with pytest.raises(requests.HTTPError):
        backend.load("x", "myrole")


def test_load_raises_on_401(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=401)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    with pytest.raises(requests.HTTPError):
        backend.load("x", "myrole")


# --- data_path + list transform --------------------------------------------
def test_data_path_dives_into_response(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={"result": {"data": {"k": "v"}}},
    )
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x", data_path="result.data")]
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_data_path_missing_returns_none(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"other": {}})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x", data_path="result.data")]
    )

    assert backend.load("x", "myrole") is None


def test_list_name_key_transforms_into_dict(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={
            "results": [
                {"name": "ntp_server", "value": "ntp.example.com"},
                {"name": "timezone", "value": "UTC"},
            ]
        },
    )
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                data_path="results",
                list_name_key="name",
                list_value_key="value",
            )
        ]
    )

    assert backend.load("x", "myrole") == {
        "ntp_server": "ntp.example.com",
        "timezone": "UTC",
    }


def test_list_value_key_defaults_to_value(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json=[{"name": "a", "value": "1"}, {"name": "b", "value": "2"}],
    )
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                list_name_key="name",  # no data_path: top-level is the list
            )
        ]
    )

    assert backend.load("x", "myrole") == {"a": "1", "b": "2"}


def test_list_transform_raises_when_not_a_list(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"results": {"not": "a list"}})
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                data_path="results",
                list_name_key="name",
            )
        ]
    )

    with pytest.raises(ValueError, match="expected list"):
        backend.load("x", "myrole")


def test_list_transform_raises_on_missing_name_key(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={"results": [{"wrong_key": "x", "value": "v"}]},
    )
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                data_path="results",
                list_name_key="name",
            )
        ]
    )

    with pytest.raises(ValueError, match="missing key 'name'"):
        backend.load("x", "myrole")


def test_list_transform_raises_on_non_dict_item(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"results": ["str", "items"]})
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                data_path="results",
                list_name_key="name",
            )
        ]
    )

    with pytest.raises(ValueError, match="must be a dict"):
        backend.load("x", "myrole")


def test_list_value_missing_becomes_none(requests_mock) -> None:
    """A list item without the value key gets None as its value — not an error."""
    requests_mock.get(
        "https://example.com/x",
        json={"results": [{"name": "k", "other": "ignored"}]},
    )
    backend = ApiBackend(
        layers=[
            _layer(
                "x",
                "https://example.com/x",
                data_path="results",
                list_name_key="name",
            )
        ]
    )

    assert backend.load("x", "myrole") == {"k": None}


# --- auth_token sugar ------------------------------------------------------
def test_auth_token_builds_bearer_header(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        auth_token="abc123",
    )

    backend.load("x", "myrole")

    assert requests_mock.last_request.headers.get("Authorization") == "Bearer abc123"


def test_auth_token_custom_header_name(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        auth_token="abc123",
        auth_header="X-API-Key",
        auth_scheme="",  # raw token, no scheme prefix
    )

    backend.load("x", "myrole")

    headers = requests_mock.last_request.headers
    assert headers.get("X-API-Key") == "abc123"
    assert "Authorization" not in headers


def test_auth_token_custom_scheme(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        auth_token="abc123",
        auth_scheme="Token",  # GitHub-style
    )

    backend.load("x", "myrole")

    assert requests_mock.last_request.headers.get("Authorization") == "Token abc123"


def test_basic_auth_still_supported(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        auth=("user", "pass"),
    )

    backend.load("x", "myrole")

    assert (
        requests_mock.last_request.headers.get("Authorization", "").startswith("Basic ")
    )


def test_auth_accepts_list_from_yaml(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        auth=["user", "pass"],
    )

    backend.load("x", "myrole")

    assert (
        requests_mock.last_request.headers.get("Authorization", "").startswith("Basic ")
    )


# --- shared vs per-layer headers -------------------------------------------
def test_shared_headers_applied_to_every_layer(requests_mock) -> None:
    requests_mock.get("https://example.com/a", json={})
    requests_mock.get("https://example.com/b", json={})
    backend = ApiBackend(
        layers=[
            _layer("a", "https://example.com/a"),
            _layer("b", "https://example.com/b"),
        ],
        headers={"X-Api-Version": "v2"},
    )

    backend.load("a", "myrole")
    backend.load("b", "myrole")

    for req in requests_mock.request_history:
        assert req.headers.get("X-Api-Version") == "v2"


def test_layer_header_overrides_shared(requests_mock) -> None:
    requests_mock.get("https://example.com/a", json={})
    backend = ApiBackend(
        layers=[
            _layer("a", "https://example.com/a", headers={"X-Api-Version": "v3"})
        ],
        headers={"X-Api-Version": "v2"},
    )

    backend.load("a", "myrole")

    assert requests_mock.last_request.headers.get("X-Api-Version") == "v3"


# --- fingerprint -----------------------------------------------------------
def test_fingerprint_uses_etag(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={"k": "v"},
        headers={"ETag": '"abc-123"'},
    )
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert backend.fingerprint("x", "myrole") == "abc-123"


def test_fingerprint_strips_etag_quotes(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={"k": "v"},
        headers={"ETag": '"tag-value"'},
    )
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    fp = backend.fingerprint("x", "myrole")
    assert fp == "tag-value"
    assert '"' not in fp


def test_fingerprint_falls_back_to_sha256(requests_mock) -> None:
    body = json.dumps({"k": "v"})
    requests_mock.get(
        "https://example.com/x",
        text=body,
        headers={"Content-Type": "application/json"},
    )
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert (
        backend.fingerprint("x", "myrole")
        == hashlib.sha256(body.encode("utf-8")).hexdigest()
    )


def test_fingerprint_none_on_404(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=404)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert backend.fingerprint("x", "myrole") is None


# --- exists ----------------------------------------------------------------
def test_exists_true_for_200(requests_mock) -> None:
    requests_mock.get("https://example.com/x", json={"k": "v"})
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert backend.exists("x", "myrole") is True


def test_exists_false_for_404(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=404)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    assert backend.exists("x", "myrole") is False


# --- identify --------------------------------------------------------------
def test_identify_returns_rendered_url() -> None:
    backend = ApiBackend(
        layers=[_layer("host", "https://example.com/hosts/{host_id}/parameters")],
        host_id=42,
    )

    assert (
        backend.identify("host", "myrole")
        == "https://example.com/hosts/42/parameters"
    )


# --- caching ---------------------------------------------------------------
def test_same_layer_hits_endpoint_once_across_calls(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/x",
        json={"k": "v"},
        headers={"ETag": '"v1"'},
    )
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    backend.load("x", "myrole")
    backend.exists("x", "myrole")
    backend.fingerprint("x", "myrole")

    assert requests_mock.call_count == 1


def test_cache_remembers_404(requests_mock) -> None:
    requests_mock.get("https://example.com/x", status_code=404)
    backend = ApiBackend(layers=[_layer("x", "https://example.com/x")])

    backend.exists("x", "myrole")
    backend.load("x", "myrole")
    backend.fingerprint("x", "myrole")

    assert requests_mock.call_count == 1


def test_different_layers_hit_different_urls(requests_mock) -> None:
    requests_mock.get("https://example.com/a", json={"ka": "va"})
    requests_mock.get("https://example.com/b", json={"kb": "vb"})
    backend = ApiBackend(
        layers=[
            _layer("a", "https://example.com/a"),
            _layer("b", "https://example.com/b"),
        ]
    )

    backend.load("a", "myrole")
    backend.load("b", "myrole")

    assert requests_mock.call_count == 2


# --- request wiring --------------------------------------------------------
def test_timeout_passed(monkeypatch: pytest.MonkeyPatch) -> None:
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
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        timeout=7.5,
    )

    backend.load("x", "myrole")

    assert captured["timeout"] == 7.5


def test_verify_tls_passed(monkeypatch: pytest.MonkeyPatch) -> None:
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
    backend = ApiBackend(
        layers=[_layer("x", "https://example.com/x")],
        verify_tls=False,
    )

    backend.load("x", "myrole")

    assert captured["verify"] is False


# --- end-to-end via MergeEngine --------------------------------------------
def test_engine_merges_layered_hierarchy(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/organizations/3/parameters",
        json={"results": [{"name": "k1", "value": "base"}, {"name": "shared", "value": "org"}]},
    )
    requests_mock.get(
        "https://example.com/domains/12/parameters",
        json={"results": [{"name": "k2", "value": "domain"}, {"name": "shared", "value": "domain"}]},
    )
    requests_mock.get(
        "https://example.com/hosts/42/parameters",
        json={"results": [{"name": "k3", "value": "host"}]},
    )

    backend = ApiBackend(
        layers=[
            _layer(
                "org",
                "https://example.com/organizations/{organization_id}/parameters",
                data_path="results",
                list_name_key="name",

            ),
            _layer(
                "domain",
                "https://example.com/domains/{domain_id}/parameters",
                data_path="results",
                list_name_key="name",

            ),
            _layer(
                "host",
                "https://example.com/hosts/{host_id}/parameters",
                data_path="results",
                list_name_key="name",

            ),
        ],
        organization_id=3, domain_id=12, host_id=42,
    )
    engine = MergeEngine(backend)

    result = engine.build("host", "myrole")

    assert result.data == {
        "k1": "base",
        "k2": "domain",
        "k3": "host",
        "shared": "domain",
    }
    assert result.sources == [
        "https://example.com/organizations/3/parameters",
        "https://example.com/domains/12/parameters",
        "https://example.com/hosts/42/parameters",
    ]


def test_engine_skips_excluded_layers(requests_mock) -> None:
    # org is excluded, so its endpoint is never requested.
    requests_mock.get(
        "https://example.com/hosts/42/parameters",
        json={"results": [{"name": "k", "value": "v"}]},
    )

    backend = ApiBackend(
        layers=[
            _layer(
                "org",
                "https://example.com/orgs/{organization_id}/parameters",
                data_path="results",
                list_name_key="name",
            ),
            _layer(
                "host",
                "https://example.com/hosts/{host_id}/parameters",
                data_path="results",
                list_name_key="name",
            ),
        ],
        host_id=42,
        excludes=["org"],
    )
    engine = MergeEngine(backend)

    result = engine.build("host", "myrole")

    assert result.data == {"k": "v"}
    # Only the host endpoint was hit
    assert requests_mock.call_count == 1


def test_engine_skips_layers_returning_404(requests_mock) -> None:
    requests_mock.get(
        "https://example.com/a", json={"results": [{"name": "k1", "value": "a"}]}
    )
    requests_mock.get("https://example.com/b", status_code=404)
    requests_mock.get(
        "https://example.com/c", json={"results": [{"name": "k2", "value": "c"}]}
    )

    backend = ApiBackend(
        layers=[
            _layer("a", "https://example.com/a",
                   data_path="results", list_name_key="name"),
            _layer("b", "https://example.com/b",
                   data_path="results", list_name_key="name"),
            _layer("c", "https://example.com/c",
                   data_path="results", list_name_key="name"),
        ]
    )
    engine = MergeEngine(backend)

    result = engine.build("c", "myrole")

    assert result.data == {"k1": "a", "k2": "c"}
    assert len(result.sources) == 2  # b (404) is not in sources


# --- registry integration --------------------------------------------------
def test_get_backend_returns_http_instance() -> None:
    from read_config_core.registry import get_backend

    backend = get_backend(
        "api",
        layers=[_layer("x", "https://example.com/x")],
    )

    assert isinstance(backend, ApiBackend)


def test_http_backend_requires_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    original = sys.modules.get("requests")
    monkeypatch.setitem(sys.modules, "requests", None)
    sys.modules.pop("read_config_core.api", None)
    try:
        from read_config_core.api import ApiBackend as FreshHTTP

        with pytest.raises(ImportError, match="requests"):
            FreshHTTP(layers=[_layer("x", "https://example.com/x")])
    finally:
        if original is not None:
            sys.modules["requests"] = original
        sys.modules.pop("read_config_core.api", None)


# --- preset integration ----------------------------------------------------
FOREMAN_HOST_URL = "https://f.test/api/v2/hosts/web01"
FOREMAN_HOST = {
    "id": 42, "organization_id": 3, "location_id": 7,
    "domain_id": 5, "subnet_id": 9, "hostgroup_id": 11,
}


@pytest.fixture
def foreman(requests_mock):
    """The one lookup the foreman adapter makes before building its chain."""
    requests_mock.get(FOREMAN_HOST_URL, json=FOREMAN_HOST)
    return requests_mock


def foreman_backend(**overrides) -> ApiBackend:
    kwargs = dict(api="foreman", base_url="https://f.test", host="web01")
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


def test_api_preset_supplies_the_layer_chain(foreman) -> None:
    names = [layer.name for layer in foreman_backend().layers]

    assert names[:2] == ["common", "organization"]
    assert names[-1] == "host"


def test_explicit_layers_are_appended_after_a_preset(foreman) -> None:
    """Hand-written layers extend an adapter, at higher precedence."""
    backend = foreman_backend(
        layers=[{"name": "site_override", "url": "https://f.test/custom"}]
    )

    names = [layer.name for layer in backend.layers]

    assert names[-1] == "site_override"
    assert "common" in names


def test_preset_options_are_forwarded(requests_mock) -> None:
    requests_mock.get("https://f.test/api/v3/hosts/web01", json=FOREMAN_HOST)

    backend = foreman_backend(preset_options={"api_version": "v3"})

    assert all("/api/v3/" in layer.url for layer in backend.layers)


def test_preset_layers_are_all_active_unless_excluded(foreman) -> None:
    backend = foreman_backend(excludes=["location", "domain", "subnet", "hostgroup"])

    assert backend.resolve_ancestry("host") == ["common", "organization", "host"]


def test_preset_is_complete_by_default(foreman) -> None:
    assert len(foreman_backend().resolve_ancestry("host")) == 7


def test_a_level_the_target_lacks_is_absent_from_the_chain(requests_mock) -> None:
    """No declaration needed: the adapter only builds levels that exist."""
    requests_mock.get(FOREMAN_HOST_URL, json=dict(FOREMAN_HOST, subnet_id=None))

    assert "subnet" not in foreman_backend().resolve_ancestry("host")


def test_excludes_rejects_an_unknown_layer_name(foreman) -> None:
    with pytest.raises(ValueError, match="unknown layer") as exc:
        foreman_backend(excludes=["hostgrp"]).layers

    assert "hostgroup" in str(exc.value)


def test_an_option_the_adapter_does_not_know_is_rejected(foreman) -> None:
    with pytest.raises(ValueError, match="unknown option") as exc:
        foreman_backend(hostt="web01").layers

    assert "host" in str(exc.value)
