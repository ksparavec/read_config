"""Unit tests for the API preset adapters.

Each adapter is a class that discovers its own layer chain, so these tests
drive ``build_layers`` with a stub fetcher and assert on the chain it produces
for a given target. Behaviour against a real server is covered in
tests/live/test_live_api.py.
"""
from __future__ import annotations

import pytest

from read_config_core.api_presets import (
    ApiPreset,
    available_api_presets,
    get_api_preset,
    register_api_preset,
)

BASE = "https://example.test"


def responder(routes: dict):
    """Fetcher stub: exact-URL lookup, None for anything absent (a 404)."""
    def _fetch(url: str):
        return routes.get(url)
    return _fetch


# --- registry --------------------------------------------------------------

def test_built_in_presets_are_registered() -> None:
    assert {"foreman", "awx", "netbox"} <= set(available_api_presets())


def test_available_presets_are_sorted() -> None:
    assert available_api_presets() == sorted(available_api_presets())


def test_unknown_preset_names_the_known_ones() -> None:
    with pytest.raises(ValueError, match="Unknown api preset: 'nope'") as exc:
        get_api_preset("nope", BASE)

    assert "foreman" in str(exc.value)


def test_preset_requires_a_base_url() -> None:
    with pytest.raises(ValueError, match="requires base_url"):
        get_api_preset("foreman", "")


def test_trailing_slash_on_base_url_is_normalized() -> None:
    assert get_api_preset("foreman", BASE + "/").base_url == BASE


def test_custom_adapter_can_be_registered() -> None:
    class MyPreset(ApiPreset):
        product = "mine"

        def build_layers(self, fetch):
            return [{"name": "only", "url": f"{self.base_url}/x"}]

    register_api_preset("unit-custom", MyPreset)
    try:
        layers = get_api_preset("unit-custom", BASE).build_layers(responder({}))

        assert layers == [{"name": "only", "url": f"{BASE}/x"}]
    finally:
        from read_config_core import api_presets

        api_presets._PRESETS.pop("unit-custom", None)


def test_base_class_requires_build_layers() -> None:
    with pytest.raises(NotImplementedError):
        ApiPreset(BASE).build_layers(responder({}))


# --- foreman ---------------------------------------------------------------

FOREMAN_HOST = {
    "id": 42, "name": "web01",
    "organization_id": 3, "location_id": 7, "domain_id": 5,
    "subnet_id": 9, "hostgroup_id": 11,
}


def foreman_layers(record=None, **options):
    record = FOREMAN_HOST if record is None else record
    version = options.get("api_version", "v2")
    preset = get_api_preset("foreman", BASE, host="web01", **options)
    lookup = f"{BASE}/api/{version}/hosts/web01"
    return preset.build_layers(responder({lookup: record}))


def test_foreman_chain_is_in_documented_precedence_order() -> None:
    names = [layer["name"] for layer in foreman_layers()]

    assert names == [
        "common", "organization", "location", "domain",
        "subnet", "hostgroup", "host",
    ]


def test_foreman_resolves_entity_ids_from_the_host_record() -> None:
    urls = {ly["name"]: ly["url"] for ly in foreman_layers()}

    assert urls["common"] == f"{BASE}/api/v2/common_parameters"
    assert urls["organization"] == f"{BASE}/api/v2/organizations/3/parameters"
    assert urls["subnet"] == f"{BASE}/api/v2/subnets/9/parameters"
    assert urls["host"] == f"{BASE}/api/v2/hosts/42/parameters"
    assert not any("{" in url for url in urls.values()), "URLs must be concrete"


def test_foreman_omits_levels_the_host_does_not_have() -> None:
    """A null entity id means that level does not exist for this host."""
    record = dict(FOREMAN_HOST, subnet_id=None, hostgroup_id=None)

    names = [layer["name"] for layer in foreman_layers(record)]

    assert names == ["common", "organization", "location", "domain", "host"]


def test_foreman_extracts_the_results_name_value_list() -> None:
    for layer in foreman_layers():
        assert layer["data_path"] == "results"
        assert layer["list_name_key"] == "name"
        assert layer["list_value_key"] == "value"


def test_foreman_requests_every_page() -> None:
    assert all(ly["params"] == {"per_page": "all"} for ly in foreman_layers())


def test_foreman_per_page_is_overridable_and_stringified() -> None:
    assert all(ly["params"] == {"per_page": "500"}
               for ly in foreman_layers(per_page=500))


def test_foreman_api_version_is_overridable() -> None:
    assert all("/api/v3/" in ly["url"] for ly in foreman_layers(api_version="v3"))


def test_foreman_requires_a_host() -> None:
    preset = get_api_preset("foreman", BASE)

    with pytest.raises(ValueError, match="requires the 'host' option"):
        preset.build_layers(responder({}))


def test_foreman_reports_an_unknown_host() -> None:
    preset = get_api_preset("foreman", BASE, host="ghost")

    with pytest.raises(ValueError, match="no host 'ghost'"):
        preset.build_layers(responder({}))


def test_foreman_rejects_an_unknown_option() -> None:
    preset = get_api_preset("foreman", BASE, host="web01", hostt="typo")

    with pytest.raises(ValueError, match="unknown option") as exc:
        preset.build_layers(responder({}))

    assert "api_version" in str(exc.value)


# --- awx -------------------------------------------------------------------

def awx_layers(routes):
    return get_api_preset("awx", BASE, host="web01").build_layers(responder(routes))


AWX_ROUTES = {
    f"{BASE}/api/v2/hosts/?name=web01": {"results": [{"id": 9, "inventory": 1}]},
    f"{BASE}/api/v2/hosts/9/groups/": {
        "results": [{"id": 2, "name": "web"}, {"id": 3, "name": "eu"}]
    },
}


def test_awx_chain_is_inventory_groups_host() -> None:
    names = [layer["name"] for layer in awx_layers(AWX_ROUTES)]

    assert names == ["inventory", "group:web", "group:eu", "host"]


def test_awx_reads_variable_data_verbatim() -> None:
    for layer in awx_layers(AWX_ROUTES):
        assert "data_path" not in layer
        assert layer["url"].endswith("/variable_data/")


def test_awx_host_in_no_groups_yields_inventory_and_host() -> None:
    routes = dict(AWX_ROUTES)
    routes[f"{BASE}/api/v2/hosts/9/groups/"] = {"results": []}

    assert [ly["name"] for ly in awx_layers(routes)] == ["inventory", "host"]


def test_awx_reports_an_unknown_host() -> None:
    with pytest.raises(ValueError, match="no host 'web01'"):
        awx_layers({f"{BASE}/api/v2/hosts/?name=web01": {"results": []}})


# --- netbox ----------------------------------------------------------------

def netbox_layers(routes):
    return get_api_preset("netbox", BASE, host="web01").build_layers(responder(routes))


def test_netbox_finds_a_device() -> None:
    layers = netbox_layers(
        {f"{BASE}/api/dcim/devices/?name=web01": {"results": [{"id": 7}]}}
    )

    assert layers == [{
        "name": "device",
        "url": f"{BASE}/api/dcim/devices/7/",
        "data_path": "config_context",
    }]


def test_netbox_falls_back_to_a_virtual_machine() -> None:
    layers = netbox_layers({
        f"{BASE}/api/dcim/devices/?name=web01": {"results": []},
        f"{BASE}/api/virtualization/virtual-machines/?name=web01": {
            "results": [{"id": 3}]
        },
    })

    assert layers[0]["name"] == "virtual_machine"
    assert layers[0]["url"] == f"{BASE}/api/virtualization/virtual-machines/3/"


def test_netbox_reports_an_unknown_target() -> None:
    with pytest.raises(ValueError, match="no device or virtual machine"):
        netbox_layers({})
