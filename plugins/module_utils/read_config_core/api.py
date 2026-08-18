"""REST API backend with layered, templated URLs.

A real REST API rarely fits one URL shape. Foreman, for example, stores
parameters under seven different endpoints (``/organizations/:id/parameters``,
``/hosts/:id/parameters``, …) and the host's final view is the merge of all
applicable entity levels. Other APIs keep role identity in a header, a query
parameter, or a token — almost never in a URL path segment.

This backend handles that by modeling every API as an **ordered list of
layers**. Each layer is a fully-configured GET request (URL, query params,
headers, response extraction) that returns one level of the hierarchy. The
``MergeEngine`` merges them in order, lowest-precedence first.

Placeholders ``{role_name}``, ``{location}`` (the current layer name) plus any
other option passed to the backend are substituted into every url/param/header
template via ``str.format`` at request time. Entity ids are therefore plain
options: ``ApiBackend(api="foreman", base_url=..., host_id=42)``.

Every configured layer is active by default. To leave one out, name it in
``excludes`` (so a host without a hostgroup declares that explicitly). A layer
whose URL template needs a value you did not supply is an error, not a
silently dropped level. So is supplying a value no layer references, which
catches a misspelled option name.

Auth: ``auth_token`` sugar builds ``{auth_scheme} {auth_token}`` into the
configured header (default ``Authorization: Bearer <token>``). Basic auth
(``auth=[user, pass]``) and raw ``headers`` are still available when
needed.

Change detection: the server's ``ETag`` header is the native fingerprint
when present; otherwise we hash the response body.

Security: *string* template values are rejected if they contain ``{`` or ``}``
to block format-string gadget attacks; non-string values pass through
unchecked. Rendered URLs can optionally be pinned via ``allowed_hosts``,
which is matched against the URL's **hostname only** (case-insensitive) --
not the scheme and not the port.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse

from .api_presets import available_api_presets, get_api_preset


def _sanitize_template_vars(values: dict[str, Any]) -> dict[str, Any]:
    """Reject template values that could exploit ``str.format``.

    A value like ``"{__class__.__mro__}"`` or ``"{0.__class__}"`` would let an
    attacker traverse Python internals or redirect requests to arbitrary hosts
    when interpolated into URLs/headers. Non-string values are passed through
    unchanged (they are converted to strings by ``str.format`` itself).
    """
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, str) and ("{" in value or "}" in value):
            raise ValueError(
                f"value for {key!r} must not contain '{{' or '}}'"
            )
        clean[key] = value
    return clean


@dataclass(frozen=True)
class ApiLayer:
    """One GET endpoint in a hierarchical HTTP config source.

    Attributes:
        name: stable identifier used for ancestry + ``files_merged``
            provenance. Must be unique within the backend.
        url: request URL template. Receives ``{role_name}``, ``{location}``,
            and every other option passed to the backend, via ``str.format``.
        params: query parameters; values are templates.
        headers: HTTP headers; values are templates. Merged on top of the
            backend's shared headers (layer wins on collision).
        data_path: dot-separated path into the JSON response. ``None`` means
            "use the top-level response object".
        list_name_key: if set, the value at ``data_path`` is expected to be
            a list; the returned config dict is ``{item[name_key]: item.get(value_key)
            for item in list}``.
        list_value_key: key whose value becomes the dict value when
            ``list_name_key`` is in use.
    """

    name: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    data_path: str | None = None
    list_name_key: str | None = None
    list_value_key: str = "value"


@dataclass(frozen=True)
class _CacheEntry:
    """Everything we need from one GET response to answer every protocol call."""

    data: dict
    etag: str | None
    raw_body: bytes


class ApiBackend:
    """ConfigBackend that fetches role configs from a REST API.

    See the module docstring for the data model. The backend is constructed
    with a list of ``ApiLayer`` specs (or plain dicts, which are coerced).
    """

    def __init__(
        self,
        layers: list[Any] | None = None,
        api: str | None = None,
        base_url: str | None = None,
        preset_options: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_token: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        auth: Any = None,
        excludes: list[str] | tuple[str, ...] | None = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        allowed_hosts: list[str] | tuple[str, ...] | None = None,
        **template_vars: Any,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ImportError(
                "ApiBackend requires 'requests'; install with 'pip install requests'."
            ) from exc

        # A preset is a vendor adapter that discovers the chain at run time
        # (it usually has to look the target up first), so its layers are
        # resolved lazily on first use. Explicit `layers` are appended after
        # whatever it returns, and therefore take precedence.
        # With a preset, any option the backend does not recognize belongs to
        # the adapter (a host name, an API version), so `host: web01` can sit
        # directly in backend_options. Without one, those options are template
        # values for the caller's own layer URLs.
        preset_kwargs = {**(preset_options or {}), **template_vars}
        self._preset = (
            get_api_preset(api, base_url or "", **preset_kwargs) if api else None
        )
        if api:
            template_vars = {}
        self._extra_layers: list[Any] = list(layers or [])
        self._resolved: tuple[ApiLayer, ...] | None = None

        if not self._preset and not self._extra_layers:
            raise ValueError(
                "provide either 'layers' or an 'api' preset "
                f"(available presets: {', '.join(available_api_presets())})"
            )
        if base_url and not api:
            raise ValueError("base_url is only meaningful together with 'api'")

        self._requested_excludes: tuple[str, ...] = tuple(excludes or ())
        self._vars: dict[str, Any] = _sanitize_template_vars(template_vars)
        self._excludes: frozenset[str] = frozenset(self._requested_excludes)

        self._shared_headers: dict[str, str] = dict(headers or {})
        if auth_token:
            prefix = f"{auth_scheme} " if auth_scheme else ""
            self._shared_headers[auth_header] = f"{prefix}{auth_token}"

        self._auth = tuple(auth) if isinstance(auth, (list, tuple)) else auth
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._allowed_hosts: frozenset[str] = (
            frozenset(allowed_hosts) if allowed_hosts else frozenset()
        )
        self._requests = requests
        self._cache: dict[tuple, _CacheEntry | None] = {}

        # Hand-written layers need no discovery, so validate them now rather
        # than on first use. A preset has to reach the server before its chain
        # exists, so its validation stays deferred.
        if self._preset is None:
            self._ensure_layers()

    @property
    def template_vars(self) -> dict[str, Any]:
        return dict(self._vars)

    @property
    def excludes(self) -> frozenset[str]:
        return self._excludes

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return self._allowed_hosts

    # --- layer resolution -------------------------------------------------
    @property
    def layers(self) -> tuple[ApiLayer, ...]:
        return self._ensure_layers()

    def _ensure_layers(self) -> tuple[ApiLayer, ...]:
        """Resolve, validate and cache the layer chain.

        A preset discovers its chain at run time, so validation that depends on
        the layers -- duplicate names, unknown excludes, unused template values
        -- can only run once they exist.
        """
        if self._resolved is not None:
            return self._resolved

        specs: list[Any] = []
        if self._preset is not None:
            specs.extend(self._preset.build_layers(self._fetch_json))
        specs.extend(self._extra_layers)

        coerced = [self._coerce_layer(spec, idx) for idx, spec in enumerate(specs)]
        seen: set[str] = set()
        for layer in coerced:
            if layer.name in seen:
                raise ValueError(f"duplicate layer name: {layer.name!r}")
            seen.add(layer.name)
        self._resolved = tuple(coerced)

        known = {layer.name for layer in self._resolved}
        unknown = [n for n in self._requested_excludes if n not in known]
        if unknown:
            raise ValueError(
                f"excludes names unknown layer(s) {unknown}; "
                f"available layers: {sorted(known)}"
            )
        if not (known - self._excludes):
            raise ValueError("excludes removes every layer; nothing left to merge")

        referenced = self._referenced_placeholders()
        unused = sorted(set(self._vars) - referenced)
        if unused:
            raise ValueError(
                f"option(s) {unused} match no placeholder in any layer URL, "
                f"query parameter, or header. Known placeholders: "
                f"{sorted(referenced) or '(none)'}."
            )
        return self._resolved

    def _fetch_json(self, url: str) -> Any:
        """GET returning decoded JSON, or None on 404. Handed to presets."""
        self._enforce_allowed_host(url)
        response = self._requests.get(
            url,
            headers=self._shared_headers or None,
            auth=self._auth,
            timeout=self._timeout,
            verify=self._verify_tls,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    # --- Protocol methods --------------------------------------------------
    def discover(self, role_name: str) -> Iterable[str]:
        """Return the deepest applicable layer name (or nothing).

        Multi-mode thus produces one result — the full-chain merge. Users who
        want partial views call single-mode with ``config_path=<layer_name>``.
        """
        applicable = self._applicable_layer_names()
        return [applicable[-1]] if applicable else []

    def resolve_ancestry(self, target: str) -> list[str]:
        applicable = self._applicable_layer_names()
        if target not in applicable:
            # Provide a helpful error so users know what's valid.
            all_names = [layer.name for layer in self._ensure_layers()]
            raise ValueError(
                f"Unknown layer {target!r}. "
                f"Applicable: {applicable}. Configured: {all_names}."
            )
        return applicable[: applicable.index(target) + 1]

    def load(self, location: str, role_name: str) -> dict | None:
        entry = self._fetch_for_layer(location, role_name)
        return entry.data if entry is not None else None

    def exists(self, location: str, role_name: str) -> bool:
        return self._fetch_for_layer(location, role_name) is not None

    def fingerprint(self, location: str, role_name: str) -> str | None:
        entry = self._fetch_for_layer(location, role_name)
        if entry is None:
            return None
        if entry.etag:
            return entry.etag.strip('"')
        return hashlib.sha256(entry.raw_body).hexdigest()

    def identify(self, location: str, role_name: str) -> str:
        layer = self._find_layer(location)
        if layer is None:
            return location
        return self._render(layer.url, role_name, location)

    # --- internal helpers --------------------------------------------------
    @staticmethod
    def _coerce_layer(layer: Any, idx: int) -> ApiLayer:
        if isinstance(layer, ApiLayer):
            return layer
        if not isinstance(layer, dict):
            raise TypeError(
                f"layer {idx}: expected dict or ApiLayer, got {type(layer).__name__}"
            )
        kwargs = dict(layer)
        kwargs.setdefault("name", str(idx))
        if "url" not in kwargs:
            raise ValueError(f"layer {kwargs['name']!r}: 'url' is required")
        return ApiLayer(**kwargs)

    _PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
    _BUILTIN_PLACEHOLDERS = frozenset({"role_name", "location"})

    def _referenced_placeholders(self) -> set[str]:
        """Placeholder names used across every configured layer."""
        found: set[str] = set()
        for layer in self._ensure_layers():
            for text in (layer.url, *layer.params.values(), *layer.headers.values()):
                found.update(self._PLACEHOLDER_RE.findall(text))
        return found - self._BUILTIN_PLACEHOLDERS

    def _applicable_layer_names(self) -> list[str]:
        return [
            layer.name for layer in self._ensure_layers()
            if layer.name not in self._excludes
        ]

    def _find_layer(self, name: str) -> ApiLayer | None:
        for layer in self._ensure_layers():
            if layer.name == name:
                return layer
        return None

    def _fetch_for_layer(
        self, location: str, role_name: str
    ) -> _CacheEntry | None:
        layer = self._find_layer(location)
        if layer is None:
            return None

        url = self._render(layer.url, role_name, layer.name)
        params = {
            key: self._render(value, role_name, layer.name)
            for key, value in layer.params.items()
        }
        headers = dict(self._shared_headers)
        for key, value in layer.headers.items():
            headers[key] = self._render(value, role_name, layer.name)

        cache_key = (url, tuple(sorted(params.items())))
        if cache_key not in self._cache:
            self._cache[cache_key] = self._do_fetch(url, params, headers, layer)
        return self._cache[cache_key]

    def _do_fetch(
        self,
        url: str,
        params: dict[str, str],
        headers: dict[str, str],
        layer: ApiLayer,
    ) -> _CacheEntry | None:
        self._enforce_allowed_host(url)
        response = self._requests.get(
            url,
            params=params or None,
            headers=headers or None,
            auth=self._auth,
            timeout=self._timeout,
            verify=self._verify_tls,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()

        raw_body = response.content
        payload = response.json()
        data = self._extract(payload, layer)
        if data is None:
            return None
        return _CacheEntry(
            data=data, etag=response.headers.get("ETag"), raw_body=raw_body
        )

    def _extract(self, payload: Any, layer: ApiLayer) -> dict | None:
        current: Any = payload
        if layer.data_path:
            for part in layer.data_path.split("."):
                if not isinstance(current, dict):
                    return None
                current = current.get(part)
                if current is None:
                    return None

        if layer.list_name_key:
            if not isinstance(current, list):
                raise ValueError(
                    f"Layer {layer.name!r}: expected list at data_path "
                    f"{layer.data_path!r}, got {type(current).__name__}"
                )
            result: dict = {}
            for item in current:
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Layer {layer.name!r}: list item must be a dict, "
                        f"got {type(item).__name__}"
                    )
                if layer.list_name_key not in item:
                    raise ValueError(
                        f"Layer {layer.name!r}: list item missing key "
                        f"{layer.list_name_key!r}"
                    )
                result[item[layer.list_name_key]] = item.get(layer.list_value_key)
            return result

        return current if isinstance(current, dict) else None

    def _render(self, template: str, role_name: str, location: str) -> str:
        try:
            return template.format(
                role_name=role_name, location=location, **self._vars
            )
        except KeyError as exc:
            missing = exc.args[0]
            raise ValueError(
                f"layer {location!r} needs {missing!r}, which was not supplied. "
                f"Either pass {missing}: <value> in backend_options, or drop the "
                f"layer with excludes: [{location!r}]."
            ) from exc

    def _enforce_allowed_host(self, url: str) -> None:
        """Block requests to hosts outside the caller-provided allowlist.

        A missing/empty ``allowed_hosts`` means "no allowlist configured" —
        all hosts are accepted (backwards compatible). Once configured, only
        the listed hostnames are reachable, matched case-insensitively.

        The comparison uses ``urlparse(url).hostname``, so entries must be
        bare hostnames. A port-qualified entry such as ``"host:8443"``
        never matches and silently blocks every request.
        """
        if not self._allowed_hosts:
            return
        host = (urlparse(url).hostname or "").lower()
        allowed = {h.lower() for h in self._allowed_hosts}
        if host not in allowed:
            raise ValueError(
                f"host {host!r} not in allowed_hosts {sorted(allowed)}; "
                f"refusing to fetch {url!r}"
            )
