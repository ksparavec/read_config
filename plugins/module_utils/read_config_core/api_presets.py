"""Vendor adapters for the API backend.

A layer chain cannot be expressed generically in YAML: every product names its
entities differently, nests them differently, and answers with a different
payload shape. Working out which levels even *exist* for a given target
usually needs a lookup of its own. So an adapter is a small Python class that
knows one API, not a static list of URLs.

The class receives the connection details and a JSON fetcher, and returns the
layer chain for the target -- every level that exists, ordered lowest
precedence first. The caller gets that whole chain merged, and narrows it with
``excludes`` if some level is unwanted::

    backend: api
    backend_options:
      api: foreman
      base_url: https://foreman.example.com
      host: "{{ inventory_hostname }}"
      excludes: [subnet]
    backend_secrets:
      auth: ["admin", "{{ foreman_password }}"]

Register an adapter for your own product with :func:`register_api_preset`:
subclass :class:`ApiPreset`, implement :meth:`ApiPreset.build_layers`, and the
backend does the rest.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

# A callable the adapter uses for its own discovery requests. Returns the
# decoded JSON body, or None if the endpoint answered 404. Supplied by the
# backend so adapters inherit its auth, timeout, TLS, and host allowlist.
FetchJson = Callable[[str], Any]


class ApiPreset:
    """Adapter for one product's configuration API.

    Subclasses are constructed with ``base_url`` plus whatever options that
    product needs (a host name, an API version), and produce the layer chain
    for that target.
    """

    #: Product name, used in error messages.
    product = "unknown"

    def __init__(self, base_url: str, **options: Any) -> None:
        if not base_url:
            raise ValueError(f"api preset {self.product!r} requires base_url")
        self.base_url = base_url.rstrip("/")
        self.options = options

    def build_layers(self, fetch: FetchJson) -> list[dict]:
        """Return the layer specs for this target, lowest precedence first.

        ``fetch`` performs a GET and returns decoded JSON (or None on 404), so
        an adapter can look up whatever it needs to decide which levels exist.
        """
        raise NotImplementedError

    # --- helpers for subclasses ------------------------------------------
    def option(self, name: str, default: Any = None, required: bool = False) -> Any:
        if required and name not in self.options:
            raise ValueError(
                f"api preset {self.product!r} requires the {name!r} option"
            )
        return self.options.get(name, default)

    def reject_unknown_options(self, known: Iterable[str]) -> None:
        """Fail on an option this adapter does not recognize (catches typos)."""
        unknown = sorted(set(self.options) - set(known))
        if unknown:
            raise ValueError(
                f"api preset {self.product!r} got unknown option(s) {unknown}; "
                f"it accepts {sorted(known)}"
            )


_PRESETS: dict[str, type[ApiPreset]] = {}


def register_api_preset(name: str, preset: type[ApiPreset]) -> None:
    """Register an adapter class under ``name``. Overwrites an existing entry."""
    _PRESETS[name] = preset


def available_api_presets() -> list[str]:
    """Return the registered adapter names, sorted."""
    return sorted(_PRESETS)


def get_api_preset(name: str, base_url: str, **options: Any) -> ApiPreset:
    """Instantiate the adapter registered under ``name``."""
    if name not in _PRESETS:
        known = ", ".join(available_api_presets()) or "(none registered)"
        raise ValueError(f"Unknown api preset: {name!r}. Known presets: {known}.")
    return _PRESETS[name](base_url, **options)


# --- Foreman ---------------------------------------------------------------

class ForemanPreset(ApiPreset):
    """Foreman parameters, resolved from the host outward.

    Foreman keeps parameters on every entity a host belongs to and merges them
    in a fixed order. Rather than making the caller enumerate those entities,
    this adapter reads the host once -- ``/api/v2/hosts/<host>`` reports its
    organization, location, domain, subnet, and hostgroup -- and builds the
    chain from what that host actually has. A host with no subnet has no
    subnet level; nothing needs to be declared for it.

    Precedence, lowest first: global (common) -> organization -> location ->
    domain -> subnet -> hostgroup -> host.
    """

    product = "foreman"

    #: layer name -> (field on the host record, endpoint collection)
    ENTITIES = (
        ("organization", "organization_id", "organizations"),
        ("location", "location_id", "locations"),
        ("domain", "domain_id", "domains"),
        ("subnet", "subnet_id", "subnets"),
        ("hostgroup", "hostgroup_id", "hostgroups"),
    )

    def build_layers(self, fetch: FetchJson) -> list[dict]:
        self.reject_unknown_options(("host", "api_version", "per_page"))
        host = self.option("host", required=True)
        api_version = self.option("api_version", "v2")
        per_page = str(self.option("per_page", "all"))
        prefix = f"{self.base_url}/api/{api_version}"

        record = fetch(f"{prefix}/hosts/{host}")
        if record is None:
            raise ValueError(
                f"foreman: no host {host!r} at {self.base_url}. Check the name "
                f"and that the credentials can read it."
            )

        def layer(name: str, path: str) -> dict:
            return {
                "name": name,
                "url": f"{prefix}/{path}",
                "params": {"per_page": per_page},
                "data_path": "results",
                "list_name_key": "name",
                "list_value_key": "value",
            }

        layers = [layer("common", "common_parameters")]
        for name, field, collection in self.ENTITIES:
            entity_id = record.get(field)
            if entity_id is not None:
                layers.append(layer(name, f"{collection}/{entity_id}/parameters"))
        layers.append(layer("host", f"hosts/{record.get('id', host)}/parameters"))
        return layers


# --- Ansible AWX / Tower ---------------------------------------------------

class AwxPreset(ApiPreset):
    """AWX host variables, resolved from the host outward.

    Reads the host to find its inventory, then its group memberships, and
    builds inventory -> group(s) -> host. Each endpoint returns the variables
    object directly, so no extraction is configured.
    """

    product = "awx"

    def build_layers(self, fetch: FetchJson) -> list[dict]:
        self.reject_unknown_options(("host", "api_version"))
        host = self.option("host", required=True)
        api_version = self.option("api_version", "v2")
        prefix = f"{self.base_url}/api/{api_version}"

        found = fetch(f"{prefix}/hosts/?name={host}")
        results = (found or {}).get("results") or []
        if not results:
            raise ValueError(f"awx: no host {host!r} at {self.base_url}")
        record = results[0]
        host_id = record["id"]

        layers: list[dict] = []
        inventory_id = record.get("inventory")
        if inventory_id is not None:
            layers.append({
                "name": "inventory",
                "url": f"{prefix}/inventories/{inventory_id}/variable_data/",
            })

        groups = fetch(f"{prefix}/hosts/{host_id}/groups/") or {}
        for group in groups.get("results") or []:
            layers.append({
                "name": f"group:{group['name']}",
                "url": f"{prefix}/groups/{group['id']}/variable_data/",
            })

        layers.append({
            "name": "host",
            "url": f"{prefix}/hosts/{host_id}/variable_data/",
        })
        return layers


# --- NetBox ----------------------------------------------------------------

class NetboxPreset(ApiPreset):
    """NetBox rendered config context for a device or virtual machine.

    NetBox merges config contexts server-side, so there is no chain to walk.
    The adapter finds the object by name -- trying devices, then virtual
    machines -- and reads its ``config_context``.
    """

    product = "netbox"

    def build_layers(self, fetch: FetchJson) -> list[dict]:
        self.reject_unknown_options(("host", "api_version"))
        host = self.option("host", required=True)
        api_version = self.option("api_version", "api")
        prefix = f"{self.base_url}/{api_version}"

        for name, path in (
            ("device", "dcim/devices"),
            ("virtual_machine", "virtualization/virtual-machines"),
        ):
            found = fetch(f"{prefix}/{path}/?name={host}")
            results = (found or {}).get("results") or []
            if results:
                return [{
                    "name": name,
                    "url": f"{prefix}/{path}/{results[0]['id']}/",
                    "data_path": "config_context",
                }]

        raise ValueError(
            f"netbox: no device or virtual machine named {host!r} at {self.base_url}"
        )


register_api_preset("foreman", ForemanPreset)
register_api_preset("awx", AwxPreset)
register_api_preset("netbox", NetboxPreset)
