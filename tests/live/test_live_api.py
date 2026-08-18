"""API backend against a real REST server (nginx serving a JSON fixture tree).

The fixture server is deliberately strict: it demands a bearer token, emits a
real ``ETag`` per resource, and 404s anything absent. That makes it possible
to exercise the parts of ``ApiBackend`` a mocked transport cannot: genuine
ETag fingerprints, genuine auth rejection, and the request cache (verified by
counting lines in nginx's access log rather than by inspecting internals).
"""
from __future__ import annotations

import socket
import subprocess
import time

import pytest

from read_config_core.base import MergeEngine
from read_config_core.api import ApiBackend

from .conftest import (
    AWX_AUTH,
    AWX_BASE,
    AWX_HOST,
    FOREMAN_AUTH,
    FOREMAN_BASE,
    FOREMAN_HOST,
    HTTP_BASE,
    HTTP_TOKEN,
    NETBOX_BASE,
    NETBOX_HOST,
    NETBOX_TOKEN,
    PODMAN,
    PREFIX,
    ROLE,
)

pytestmark = pytest.mark.live

requests = pytest.importorskip("requests")

# Foreman-shaped layers: a list of {"name":..., "value":...} under "results".
FOREMAN_LAYERS = [
    {
        "name": "organization",
        "url": HTTP_BASE + "/v1/organizations/{organization_id}/parameters",
        "data_path": "results",
        "list_name_key": "name",
    },
    {
        "name": "location",
        "url": HTTP_BASE + "/v1/locations/{location_id}/parameters",
        "data_path": "results",
        "list_name_key": "name",
    },
    {
        "name": "hostgroup",
        "url": HTTP_BASE + "/v1/hostgroups/{hostgroup_id}/parameters",
        "data_path": "results",
        "list_name_key": "name",
    },
    {
        "name": "host",
        "url": HTTP_BASE + "/v1/hosts/{host_id}/parameters",
        "data_path": "results",
        "list_name_key": "name",
    },
]

FULL_IDS = {
    "organization_id": 3,
    "location_id": 7,
    "hostgroup_id": 11,
    "host_id": 42,
}


def make_backend(**overrides) -> ApiBackend:
    """The Foreman-shaped chain, unless the caller supplies its own layers.

    The entity ids are only added for the default chain: every supplied value
    must be referenced by some layer, so spreading them over a caller's
    single-layer override would be rejected.
    """
    kwargs: dict = {"auth_token": HTTP_TOKEN, "allowed_hosts": ["127.0.0.1"]}
    if "layers" not in overrides and "api" not in overrides:
        kwargs["layers"] = FOREMAN_LAYERS
        kwargs.update(FULL_IDS)
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


def nginx_request_count(path: str) -> int:
    """Count access-log lines for ``path`` (the server is otherwise idle)."""
    proc = subprocess.run(
        [PODMAN, "logs", f"{PREFIX}-nginx"], capture_output=True, text=True
    )
    return ((proc.stdout or "") + (proc.stderr or "")).count(f"GET {path} ")


# --- the layered merge -----------------------------------------------------

def test_full_layer_chain_merges_in_order() -> None:
    backend = make_backend()

    data = MergeEngine(backend).build("host", ROLE).data

    assert data == {
        "listen_port": 8080,                     # organization only
        "log_level": "warn",                     # location beats organization
        "region": "eu-west",                     # location only
        "role_tier": "frontend",                 # hostgroup only
        "workers": 8,                            # host beats hostgroup beats org
        "hostname": "web01.eu-west.internal",    # host only
        "database": {                            # deep-merged org + host
            "pool_size": 50,
            "host": "db.default.internal",
        },
    }


def test_ancestry_is_the_applicable_layer_prefix() -> None:
    backend = make_backend()

    assert backend.resolve_ancestry("hostgroup") == [
        "organization",
        "location",
        "hostgroup",
    ]


def test_discover_reports_only_the_deepest_layer() -> None:
    """Multi-mode therefore yields exactly one, fully merged, result."""
    backend = make_backend()

    assert list(backend.discover(ROLE)) == ["host"]


def test_identify_is_the_rendered_url() -> None:
    backend = make_backend()

    assert backend.identify("host", ROLE) == f"{HTTP_BASE}/v1/hosts/42/parameters"


# --- excludes ---------------------------------------------------------------

def test_excluded_layer_does_not_contribute() -> None:
    """A host with no hostgroup declares that with excludes."""
    backend = make_backend(excludes=["hostgroup"])

    data = MergeEngine(backend).build("host", ROLE).data

    assert "role_tier" not in data, "excluded layer still contributed"
    assert backend.resolve_ancestry("host") == ["organization", "location", "host"]


def test_naming_an_excluded_layer_as_the_target_is_an_error() -> None:
    backend = make_backend(excludes=["hostgroup"])

    with pytest.raises(ValueError, match="Unknown layer 'hostgroup'"):
        backend.resolve_ancestry("hostgroup")


def test_missing_template_value_is_a_hard_error_not_a_silent_skip() -> None:
    """The regression the excludes model exists to prevent.

    Under the old opt-in model, omitting hostgroup_id silently dropped the
    hostgroup level and the task still succeeded.
    """
    kwargs = {k: v for k, v in FULL_IDS.items() if k != "hostgroup_id"}
    backend = ApiBackend(
        layers=FOREMAN_LAYERS, auth_token=HTTP_TOKEN,
        allowed_hosts=["127.0.0.1"], **kwargs,
    )

    with pytest.raises(ValueError, match="needs 'hostgroup_id'"):
        MergeEngine(backend).build("host", ROLE)


# --- response extraction ---------------------------------------------------

def test_plain_object_response_needs_no_data_path() -> None:
    backend = make_backend(
        layers=[{"name": "defaults", "url": f"{HTTP_BASE}/v1/defaults/global"}],
    )

    assert backend.load("defaults", ROLE) == {"timezone": "UTC", "retries": 3}


def test_dotted_data_path_traverses_nested_objects() -> None:
    backend = make_backend(
        layers=[
            {
                "name": "deep",
                "url": f"{HTTP_BASE}/v1/nested/deep",
                "data_path": "payload.parameters",
            }
        ],
    )

    assert backend.load("deep", ROLE) == {"nested_key": "nested_value", "workers": 99}


def test_missing_data_path_segment_yields_no_data() -> None:
    backend = make_backend(
        layers=[
            {
                "name": "deep",
                "url": f"{HTTP_BASE}/v1/nested/deep",
                "data_path": "payload.absent",
            }
        ],
    )

    assert backend.load("deep", ROLE) is None
    assert backend.exists("deep", ROLE) is False


def test_list_projection_uses_name_and_value_keys() -> None:
    backend = make_backend()

    organization = backend.load("organization", ROLE)

    assert organization["workers"] == 2
    assert organization["database"] == {
        "pool_size": 10,
        "host": "db.default.internal",
    }


# --- status handling -------------------------------------------------------

def test_absent_endpoint_404s_into_an_absent_layer() -> None:
    """404 means 'this level has no config', not 'the run failed'."""
    backend = make_backend(
        layers=[
            {"name": "organization", "url": f"{HTTP_BASE}/v1/organizations/3/parameters",
             "data_path": "results", "list_name_key": "name"},
            {"name": "ghost", "url": f"{HTTP_BASE}/v1/hostgroups/999/parameters",
             "data_path": "results", "list_name_key": "name"},
        ],
    )

    assert backend.load("ghost", ROLE) is None
    assert backend.exists("ghost", ROLE) is False
    # ...and the chain still merges the layers that do exist.
    assert MergeEngine(backend).build("ghost", ROLE).data["workers"] == 2


def test_non_404_errors_propagate() -> None:
    """A 401 is a real failure and must not be mistaken for an empty layer."""
    backend = make_backend(auth_token="wrong-token")

    with pytest.raises(requests.HTTPError):
        backend.load("organization", ROLE)


def test_missing_token_is_rejected_by_the_server() -> None:
    backend = make_backend(auth_token=None)

    with pytest.raises(requests.HTTPError):
        backend.load("organization", ROLE)


def test_custom_auth_header_and_scheme_are_used() -> None:
    """The fixture server wants exactly 'Authorization: Bearer <token>'."""
    backend = make_backend(
        auth_token=HTTP_TOKEN, auth_header="Authorization", auth_scheme="Bearer"
    )

    assert backend.load("organization", ROLE) is not None


# --- fingerprints ----------------------------------------------------------

def test_fingerprint_is_the_servers_etag() -> None:
    backend = make_backend()
    response = requests.get(
        f"{HTTP_BASE}/v1/hosts/42/parameters",
        headers={"Authorization": f"Bearer {HTTP_TOKEN}"},
        timeout=10,
    )
    etag = response.headers["ETag"].strip('"')

    assert backend.fingerprint("host", ROLE) == etag


def test_fingerprint_is_stable_across_fresh_backends() -> None:
    assert make_backend().fingerprint("host", ROLE) == make_backend().fingerprint(
        "host", ROLE
    )


# --- request cache ---------------------------------------------------------

def test_repeated_reads_hit_the_server_once_per_invocation() -> None:
    """ApiBackend caches per (url, params) for the life of the backend."""
    path = "/v1/hosts/42/parameters"
    backend = make_backend()
    before = nginx_request_count(path)

    backend.load("host", ROLE)
    backend.load("host", ROLE)
    backend.exists("host", ROLE)
    backend.fingerprint("host", ROLE)

    assert nginx_request_count(path) - before == 1


def test_a_new_backend_refetches() -> None:
    """The cache is per-instance, so a later play sees fresh data."""
    path = "/v1/hosts/42/parameters"
    before = nginx_request_count(path)

    make_backend().load("host", ROLE)
    make_backend().load("host", ROLE)

    assert nginx_request_count(path) - before == 2


# --- security --------------------------------------------------------------

def test_allowed_hosts_blocks_an_off_allowlist_host() -> None:
    backend = make_backend(
        layers=[{"name": "evil", "url": "http://169.254.169.254/latest/meta-data"}],
        allowed_hosts=["127.0.0.1"],
    )

    with pytest.raises(ValueError, match="not in allowed_hosts"):
        backend.load("evil", ROLE)


def test_allowed_hosts_matches_hostname_only_not_port() -> None:
    """The allowlist pins the host; it does not pin scheme or port."""
    backend = make_backend(allowed_hosts=["127.0.0.1"])

    # Same host, different port: permitted by the allowlist, so the request is
    # attempted and fails at the connection layer rather than the guard.
    off_port = make_backend(
        layers=[{"name": "other", "url": "http://127.0.0.1:19999/v1/x"}],
        allowed_hosts=["127.0.0.1"],
    )
    with pytest.raises(requests.ConnectionError):
        off_port.load("other", ROLE)

    assert backend.load("organization", ROLE) is not None


def test_template_values_containing_braces_are_rejected() -> None:
    with pytest.raises(ValueError, match="must not contain"):
        make_backend(host_id="{__class__.__mro__}")


def test_role_name_scopes_a_layer_only_when_templated() -> None:
    """{role_name} in the URL is what makes a layer role-specific."""
    templated = make_backend(
        layers=[
            {
                "name": "byrole",
                "url": HTTP_BASE + "/v1/hier/{role_name}/parameters",
            }
        ],
    )

    # "global" exists as a fixture path; an unknown role 404s into None.
    assert templated.load("byrole", "global") == {
        "listen_port": 8080,
        "workers": 2,
        "log_level": "info",
        "database": {"pool_size": 10, "host": "db.default.internal"},
    }
    assert templated.load("byrole", "no-such-role") is None


@pytest.fixture
def blackhole_port():
    """A socket that accepts connections and then never answers.

    Gives the timeout test a deterministic target: the TCP handshake always
    completes, so the request is guaranteed to hang until the read timeout
    fires, rather than racing against how fast localhost happens to be.
    """
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        yield server.getsockname()[1]
    finally:
        server.close()


def test_timeout_bounds_a_hanging_request(blackhole_port) -> None:
    backend = make_backend(
        layers=[{"name": "slow", "url": f"http://127.0.0.1:{blackhole_port}/v1/x"}],
        timeout=1.0,
    )

    started = time.monotonic()
    with pytest.raises(requests.Timeout):
        backend.load("slow", ROLE)

    assert time.monotonic() - started < 10, "timeout was not applied"


# --- API presets against a live server -------------------------------------
#
# Each adapter discovers its own chain: it looks the target up, sees which
# levels exist, and builds the layers from that. The fixture tree mirrors the
# real response shapes, so these exercise the adapters end to end rather than
# only as layer factories.

def preset_backend(api: str, **overrides) -> ApiBackend:
    kwargs = dict(
        api=api, base_url=HTTP_BASE, host="web01",
        auth_token=HTTP_TOKEN, allowed_hosts=["127.0.0.1"],
    )
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


# --- foreman: a real Foreman 3.16.0 -----------------------------------------

def foreman_backend(**overrides) -> ApiBackend:
    kwargs = dict(
        api="foreman", base_url=FOREMAN_BASE, host=FOREMAN_HOST,
        auth=list(FOREMAN_AUTH), allowed_hosts=["127.0.0.1"],
    )
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


def test_foreman_adapter_discovers_the_chain_from_the_real_host() -> None:
    """One lookup of /api/v2/hosts/<name> yields every level it belongs to."""
    backend = foreman_backend()

    assert [layer.name for layer in backend.layers] == [
        "common", "organization", "location", "domain", "hostgroup", "host",
    ]


def test_foreman_host_is_addressable_by_name() -> None:
    """The adapter passes inventory_hostname straight through, not an id."""
    identifier = foreman_backend().identify("host", ROLE)

    # Foreman resolved the name to its numeric id for the parameters endpoint.
    assert identifier.startswith(f"{FOREMAN_BASE}/api/v2/hosts/")
    assert identifier.endswith("/parameters")


def test_foreman_omits_a_level_the_host_does_not_have() -> None:
    """The seeded host has no subnet, so Foreman reports subnet_id: null."""
    assert "subnet" not in foreman_backend().resolve_ancestry("host")


def test_foreman_merges_real_parameters_in_precedence_order() -> None:
    data = MergeEngine(foreman_backend()).build("host", ROLE).data

    # workers is set at three levels: organization 2, hostgroup 4, host 8.
    assert data["workers"] == "8"
    assert data["role_tier"] == "frontend"     # hostgroup only
    assert data["region"] == "org-wide"        # organization only
    assert data["log_level"] == "warn"         # location only
    assert data["search_domain"] == "eu.example.com"   # domain only
    assert data["hostname"] == "web01"         # host only


def test_foreman_includes_its_own_seeded_global_parameters() -> None:
    """common_parameters is Foreman's real global level, not something we set."""
    data = MergeEngine(foreman_backend()).build("host", ROLE).data

    assert "host_registration_insights" in data


def test_foreman_resolves_concrete_parameter_endpoints() -> None:
    sources = MergeEngine(foreman_backend()).build("host", ROLE).sources

    assert sources[0] == f"{FOREMAN_BASE}/api/v2/common_parameters"
    assert sources[-1].endswith("/parameters")
    assert all("{" not in source for source in sources)
    assert len(sources) == 6


def test_excludes_filters_a_discovered_level() -> None:
    backend = foreman_backend(excludes=["domain", "hostgroup"])

    data = MergeEngine(backend).build("host", ROLE).data

    assert backend.resolve_ancestry("host") == [
        "common", "organization", "location", "host",
    ]
    assert "search_domain" not in data     # domain level excluded
    assert "role_tier" not in data         # hostgroup level excluded
    assert data["workers"] == "8"          # host level still wins


def test_foreman_reports_an_unknown_host() -> None:
    with pytest.raises(ValueError, match="no host 'ghost.example.com'"):
        foreman_backend(host="ghost.example.com").layers


def test_foreman_adapter_still_honours_auth() -> None:
    with pytest.raises(requests.HTTPError):
        foreman_backend(auth=["admin", "wrong-password"]).layers


def test_a_preset_can_be_extended_with_a_hand_written_layer() -> None:
    """A site-specific endpoint layered on top of the vendor adapter.

    Uses another Foreman endpoint so the one set of credentials applies; a
    real deployment would point this at its own CMDB.
    """
    backend = foreman_backend(
        layers=[{
            "name": "site_override",
            "url": f"{FOREMAN_BASE}/api/v2/organizations/1/parameters",
            "params": {"per_page": "all"},
            "data_path": "results",
            "list_name_key": "name",
            "list_value_key": "value",
        }],
    )

    chain = backend.resolve_ancestry("site_override")
    data = MergeEngine(backend).build("site_override", ROLE).data

    assert chain[-1] == "site_override", "explicit layers come last"
    assert chain[0] == "common", "the adapter's chain still comes first"
    assert data["role_tier"] == "frontend"   # still merged from Foreman
    assert data["workers"] == "2"            # the extra layer overrode host 8


# --- awx: a real AWX 24.6.1 -------------------------------------------------

def awx_backend(**overrides) -> ApiBackend:
    kwargs = dict(
        api="awx", base_url=AWX_BASE, host=AWX_HOST,
        auth=list(AWX_AUTH), allowed_hosts=["127.0.0.1"],
    )
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


def test_awx_adapter_discovers_inventory_groups_and_host() -> None:
    """The host lookup yields its inventory, then its group memberships."""
    assert [layer.name for layer in awx_backend().layers] == [
        "inventory", "group:web", "host",
    ]


def test_awx_merges_variable_data_in_ansible_precedence() -> None:
    result = MergeEngine(awx_backend()).build("host", ROLE)

    # workers is set at all three levels: inventory 2, group 4, host 8.
    assert result.data["workers"] == 8
    assert result.data["listen_port"] == 8080     # inventory only
    assert result.data["role_tier"] == "frontend" # group only
    assert result.data["hostname"] == "web01"     # host only
    assert [s.rsplit("/api/v2/", 1)[1] for s in result.sources] == [
        "inventories/1/variable_data/",
        "groups/1/variable_data/",
        "hosts/1/variable_data/",
    ]


def test_awx_reads_variable_data_verbatim() -> None:
    """variable_data returns the variables object itself: no extraction."""
    assert awx_backend().load("inventory", ROLE) == {
        "listen_port": 8080, "workers": 2, "log_level": "info",
    }


def test_awx_preserves_json_types() -> None:
    """Unlike Foreman parameters, AWX variables keep their JSON types."""
    data = MergeEngine(awx_backend()).build("host", ROLE).data

    assert isinstance(data["workers"], int)


def test_awx_group_level_can_be_excluded() -> None:
    backend = awx_backend(excludes=["group:web"])

    data = MergeEngine(backend).build("host", ROLE).data

    assert backend.resolve_ancestry("host") == ["inventory", "host"]
    assert "role_tier" not in data


def test_awx_reports_an_unknown_host() -> None:
    with pytest.raises(ValueError, match="no host 'ghost'"):
        awx_backend(host="ghost").layers


# --- netbox: a real NetBox 4.6.8 --------------------------------------------

def netbox_backend(**overrides) -> ApiBackend:
    kwargs = dict(
        api="netbox", base_url=NETBOX_BASE, host=NETBOX_HOST,
        auth_token=NETBOX_TOKEN, allowed_hosts=["127.0.0.1"],
    )
    kwargs.update(overrides)
    return ApiBackend(**kwargs)


def test_netbox_adapter_finds_the_device() -> None:
    backend = netbox_backend()

    assert [layer.name for layer in backend.layers] == ["device"]
    assert backend.identify("device", ROLE).endswith("/api/dcim/devices/1/")


def test_netbox_reads_the_server_rendered_config_context() -> None:
    """NetBox merges its contexts itself; the adapter just extracts them.

    The seeded site context (weight 100) and role context (weight 200) are
    combined by NetBox, including a deep merge of the nested database dict.
    """
    data = MergeEngine(netbox_backend()).build("device", ROLE).data

    assert data == {
        "listen_port": 8080,                    # site context only
        "workers": 8,                           # role context overrode site
        "log_level": "warn",                    # role context overrode site
        "database": {                           # deep-merged by NetBox
            "pool_size": 50,
            "host": "db.default.internal",
        },
    }


def test_netbox_uses_a_v2_bearer_token() -> None:
    """NetBox 4.6 tokens are presented as 'Bearer nbt_<key>.<token>'."""
    with pytest.raises(requests.HTTPError):
        netbox_backend(auth_token="nbt_rcliveKey123.wrong").layers


def test_netbox_reports_an_unknown_target() -> None:
    with pytest.raises(ValueError, match="no device or virtual machine"):
        netbox_backend(host="ghost").layers
