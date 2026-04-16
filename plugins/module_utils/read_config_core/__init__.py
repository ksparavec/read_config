"""Backend abstractions and merging engine for the read_config module."""
from .base import ConfigBackend, Location, MergeEngine, MergeResult
from .cache import ConfigCache
from .filesystem import (
    FilesystemBackend,
    find_directories_with_role_config,
    get_config_file_if_exists,
    validate_path_security,
)
from .registry import available_backends, get_backend, register_backend
from .sql import SQLBackend

__all__ = [
    "ConfigBackend",
    "ConfigCache",
    "FilesystemBackend",
    "Location",
    "MergeEngine",
    "MergeResult",
    "SQLBackend",
    "available_backends",
    "find_directories_with_role_config",
    "get_backend",
    "get_config_file_if_exists",
    "register_backend",
    "validate_path_security",
]
