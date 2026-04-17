"""HTTP/REST backend with layered, templated URLs.

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
keys in the user-supplied ``context`` dict are substituted into every
url/param/header template via ``str.format`` at request time.

Layers declare ``required_context``; if any key is missing, the layer is
skipped (so a host without a hostgroup simply doesn't include that layer).

Auth: ``auth_token`` sugar builds ``{auth_scheme} {auth_token}`` into the
configured header (default ``Authorization: Bearer <token>``). Basic auth
(``auth=[user, pass]``) and raw ``headers`` are still available when
needed.

Change detection: the server's ``ETag`` header is the native fingerprint
when present; otherwise we hash the response body.

Security: context string values are rejected if they contain ``{`` or ``}``
to block format-string gadget attacks. Rendered URLs can optionally be
pinned to an allowlist of (scheme, host) tuples via ``allowed_hosts``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import urlparse


def _sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    """Reject context string values that could exploit ``str.format``.

    A value like ``"{__class__.__mro__}"`` or ``"{0.__class__}"`` would let an
    attacker traverse Python internals or redirect requests to arbitrary hosts
    when interpolated into URLs/headers. Non-string values are passed through
    unchanged (they are converted to strings by ``str.format`` itself).
    """
    clean: dict[str, Any] = {}
    for key, value in context.items():
        if isinstance(value, str) and ("{" in value or "}" in value):
            raise ValueError(
                f"context value for {key!r} must not contain '{{' or '}}'"
            )
        clean[key] = value
    return clean


@dataclass(frozen=True)
class HTTPLayer:
    """One GET endpoint in a hierarchical HTTP config source.

    Attributes:
        name: stable identifier used for ancestry + ``files_merged``
            provenance. Must be unique within the backend.
        url: request URL template. Receives ``{role_name}``, ``{location}``,
            and every key from the backend's ``context`` dict via ``str.format``.
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
        required_context: context keys that must be present for this layer
            to apply. Missing keys → the layer is silently skipped.
    """

    name: str
    url: str
    params: dict[str, str] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    data_path: str | None = None
    list_name_key: str | None = None
    list_value_key: str = "value"
    required_context: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CacheEntry:
    """Everything we need from one GET response to answer every protocol call."""

    data: dict
    etag: str | None
    raw_body: bytes


class HTTPBackend:
    """ConfigBackend that fetches role configs from a REST API.

    See the module docstring for the data model. The backend is constructed
    with a list of ``HTTPLayer`` specs (or plain dicts, which are coerced).
    """

    def __init__(
        self,
        layers: list[Any],
        context: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        auth_token: str | None = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        auth: Any = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        allowed_hosts: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ImportError(
                "HTTPBackend requires 'requests'; install with 'pip install requests'."
            ) from exc

        if not layers:
            raise ValueError("layers must be a non-empty list")

        coerced: list[HTTPLayer] = [
            self._coerce_layer(layer, idx) for idx, layer in enumerate(layers)
        ]
        seen: set[str] = set()
        for layer in coerced:
            if layer.name in seen:
                raise ValueError(f"duplicate layer name: {layer.name!r}")
            seen.add(layer.name)
        self._layers: tuple[HTTPLayer, ...] = tuple(coerced)

        self._context: dict[str, Any] = _sanitize_context(context or {})
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

    @property
    def layers(self) -> tuple[HTTPLayer, ...]:
        return self._layers

    @property
    def context(self) -> dict[str, Any]:
        return dict(self._context)

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return self._allowed_hosts

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
            all_names = [layer.name for layer in self._layers]
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
    def _coerce_layer(layer: Any, idx: int) -> HTTPLayer:
        if isinstance(layer, HTTPLayer):
            return layer
        if not isinstance(layer, dict):
            raise TypeError(
                f"layer {idx}: expected dict or HTTPLayer, got {type(layer).__name__}"
            )
        kwargs = dict(layer)
        kwargs.setdefault("name", str(idx))
        if "required_context" in kwargs:
            kwargs["required_context"] = tuple(kwargs["required_context"])
        if "url" not in kwargs:
            raise ValueError(f"layer {kwargs['name']!r}: 'url' is required")
        return HTTPLayer(**kwargs)

    def _applicable_layer_names(self) -> list[str]:
        return [
            layer.name
            for layer in self._layers
            if all(key in self._context for key in layer.required_context)
        ]

    def _find_layer(self, name: str) -> HTTPLayer | None:
        for layer in self._layers:
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
        layer: HTTPLayer,
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

    def _extract(self, payload: Any, layer: HTTPLayer) -> dict | None:
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
        return template.format(
            role_name=role_name, location=location, **self._context
        )

    def _enforce_allowed_host(self, url: str) -> None:
        """Block requests to hosts outside the caller-provided allowlist.

        A missing/empty ``allowed_hosts`` means "no allowlist configured" —
        all hosts are accepted (backwards compatible). Once configured, only
        hosts (``netloc``) listed are reachable. Case-insensitive match.
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
