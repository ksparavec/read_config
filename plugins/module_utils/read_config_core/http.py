"""HTTP/REST backend for ConfigBackend.

URL shape: ``{base_url}/{role_name}/{location}``. Location uses ``/`` as path
separator; ancestry is derived from successive path prefixes, the same model
KVBackend uses.

Per-instance response cache ensures ``load`` + ``fingerprint`` + ``exists``
for the same location issue at most one HTTP GET during a single Ansible
module run.

Dependency: ``requests`` is imported lazily so filesystem-only users don't
need it installed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import quote


@dataclass(frozen=True)
class _CacheEntry:
    """Everything we need from one GET response to answer every protocol call."""

    data: dict
    etag: str | None
    raw_body: bytes


class HTTPBackend:
    """Fetch role configs from a REST API.

    Response shape: by default the endpoint must return a JSON object that
    becomes the config data directly. For wrapped APIs, set ``data_path``
    to a dot-separated path (e.g. ``"result.data"``) to extract the nested
    object.

    Discovery: if ``discover_url`` is provided it must return a JSON array of
    location strings. ``{role}`` in the URL is substituted per invocation.
    Without a discovery URL, the module works in single-location mode only.

    Change detection: uses the server's ``ETag`` header when present
    (stripped of surrounding quotes); otherwise falls back to sha256 of the
    response body.

    404 semantics: a 404 on the config endpoint is treated as "no data here"
    (``load`` returns ``None``). Any other 4xx/5xx raises.
    """

    def __init__(
        self,
        base_url: str,
        discover_url: str | None = None,
        headers: dict[str, str] | None = None,
        auth: Any = None,
        timeout: float = 10.0,
        verify_tls: bool = True,
        data_path: str | None = None,
    ) -> None:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ImportError(
                "HTTPBackend requires 'requests'; install with 'pip install requests'."
            ) from exc

        if not base_url:
            raise ValueError("base_url must be non-empty")

        self._base_url = base_url.rstrip("/")
        self._discover_url = discover_url
        self._headers = dict(headers) if headers else {}
        # auth may come in as a list from YAML/JSON; normalize to tuple for requests.
        self._auth = tuple(auth) if isinstance(auth, (list, tuple)) else auth
        self._timeout = timeout
        self._verify_tls = verify_tls
        self._data_path = data_path
        self._requests = requests
        self._cache: dict[str, _CacheEntry | None] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def discover_url(self) -> str | None:
        return self._discover_url

    # --- Protocol methods --------------------------------------------------
    def discover(self, role_name: str) -> Iterable[str]:
        if not self._discover_url:
            return []
        url = self._resolve_discover_url(role_name)
        response = self._get(url)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(
                f"Discovery endpoint {url!r} did not return a JSON array"
            )
        for loc in payload:
            if not isinstance(loc, str):
                raise ValueError(
                    f"Discovery endpoint {url!r} returned non-string location: {loc!r}"
                )
        return payload

    def resolve_ancestry(self, target: str) -> list[str]:
        if not target:
            return [""]
        parts = [p for p in target.split("/") if p != ""]
        chain: list[str] = []
        current = ""
        for part in parts:
            current = f"{current}/{part}" if current else part
            chain.append(current)
        return chain

    def load(self, location: str, role_name: str) -> dict | None:
        entry = self._fetch(self._url_for(role_name, location))
        return entry.data if entry is not None else None

    def exists(self, location: str, role_name: str) -> bool:
        return self._fetch(self._url_for(role_name, location)) is not None

    def fingerprint(self, location: str, role_name: str) -> str | None:
        entry = self._fetch(self._url_for(role_name, location))
        if entry is None:
            return None
        if entry.etag:
            return entry.etag.strip('"')
        return hashlib.sha256(entry.raw_body).hexdigest()

    def identify(self, location: str, role_name: str) -> str:
        return self._url_for(role_name, location)

    # --- internal helpers --------------------------------------------------
    def _url_for(self, role_name: str, location: str) -> str:
        role_segment = quote(role_name, safe="")
        if not location:
            return f"{self._base_url}/{role_segment}"
        # Preserve "/" inside location so path structure survives URL-encoding.
        location_segment = quote(location, safe="/")
        return f"{self._base_url}/{role_segment}/{location_segment}"

    def _resolve_discover_url(self, role_name: str) -> str:
        url = self._discover_url or ""
        if "{role}" in url:
            return url.replace("{role}", quote(role_name, safe=""))
        return url

    def _fetch(self, url: str) -> _CacheEntry | None:
        if url not in self._cache:
            self._cache[url] = self._do_fetch(url)
        return self._cache[url]

    def _do_fetch(self, url: str) -> _CacheEntry | None:
        response = self._get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()

        raw_body = response.content
        payload = response.json()
        extracted = self._extract(payload)
        if extracted is None:
            return None
        return _CacheEntry(
            data=extracted,
            etag=response.headers.get("ETag"),
            raw_body=raw_body,
        )

    def _get(self, url: str):
        return self._requests.get(
            url,
            headers=self._headers,
            auth=self._auth,
            timeout=self._timeout,
            verify=self._verify_tls,
        )

    def _extract(self, payload: Any) -> dict | None:
        if self._data_path is None:
            return payload if isinstance(payload, dict) else None
        current: Any = payload
        for part in self._data_path.split("."):
            if not isinstance(current, dict):
                return None
            current = current.get(part)
            if current is None:
                return None
        return current if isinstance(current, dict) else None
