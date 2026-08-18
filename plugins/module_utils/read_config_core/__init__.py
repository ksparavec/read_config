"""Backend abstractions and merging engine for the read_config module."""
from .base import ConfigBackend, Location, MergeEngine, MergeResult
from .cache import ConfigCache
from .filesystem import (
    FilesystemBackend,
    find_directories_with_role_config,
    get_config_file_if_exists,
    validate_path_security,
)
from .api import ApiBackend, ApiLayer
from .api_presets import (
    ApiPreset,
    available_api_presets,
    get_api_preset,
    register_api_preset,
)
from .kv import InMemoryKVClient, KVBackend, KVClient
from .kv_consul import ConsulKVClient, make_consul_backend
from .kv_etcd import EtcdKVClient, make_etcd_backend
from .kv_redis import RedisKVClient, make_redis_backend
from .registry import available_backends, get_backend, register_backend
from .sql import SQLBackend

__all__ = [
    "ApiBackend",
    "ApiPreset",
    "ApiLayer",
    "ConfigBackend",
    "ConfigCache",
    "ConsulKVClient",
    "EtcdKVClient",
    "FilesystemBackend",
    "InMemoryKVClient",
    "KVBackend",
    "KVClient",
    "Location",
    "MergeEngine",
    "MergeResult",
    "RedisKVClient",
    "SQLBackend",
    "available_api_presets",
    "available_backends",
    "get_api_preset",
    "find_directories_with_role_config",
    "get_backend",
    "get_config_file_if_exists",
    "make_consul_backend",
    "make_etcd_backend",
    "make_redis_backend",
    "register_api_preset",
    "register_backend",
    "validate_path_security",
]
