"""Backend registry: map a backend name to a factory callable."""
from __future__ import annotations

from typing import Any, Callable

from .base import ConfigBackend
from .filesystem import FilesystemBackend

BackendFactory = Callable[..., ConfigBackend]

_REGISTRY: dict[str, BackendFactory] = {}


def register_backend(name: str, factory: BackendFactory) -> None:
    """Register a backend factory under ``name``. Overwrites existing entries.

    ``factory`` must accept keyword arguments only and return a
    ``ConfigBackend`` instance. Typical factories are just the backend class
    itself (``register_backend("sql", SQLBackend)``).
    """
    _REGISTRY[name] = factory


def get_backend(name: str, **options: Any) -> ConfigBackend:
    """Instantiate the backend registered under ``name`` with the given options."""
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "(none registered)"
        raise ValueError(f"Unknown backend: {name!r}. Known backends: {known}.")
    return _REGISTRY[name](**options)


def available_backends() -> list[str]:
    """Return the list of registered backend names, sorted."""
    return sorted(_REGISTRY)


# Seed with the default filesystem backend.
register_backend("filesystem", FilesystemBackend)
