"""Shared conformance contract every ConfigBackend implementation must satisfy.

The contract is split into three classes so applicability is structural rather
than runtime-skipped:

* ``BackendContract`` — invariants every backend must honor.
* ``ValidatesTargetsContract`` — additional test for backends that reject
  malformed targets (filesystem path traversal, named-layer lookups, etc.).
  Subclasses must override the ``invalid_location`` fixture.
* ``ContentAwareDiscoveryContract`` — additional test for backends whose
  ``discover()`` examines stored data (an unknown role has no data and
  discovery returns empty). Backends that list structural units (e.g. HTTP
  layers) don't satisfy this.

A new backend adds one subclass that inherits from ``BackendContract`` plus
whichever optional mixins apply, and provides the three required fixtures::

    class TestMyBackendContract(BackendContract, ValidatesTargetsContract):
        @pytest.fixture
        def backend(self, tmp_path):
            return MyBackend(...)

        @pytest.fixture
        def populated_location(self, backend):
            return "loc-with-data"

        @pytest.fixture
        def empty_location(self, backend):
            return "loc-without-data"

        @pytest.fixture
        def invalid_location(self, backend):  # only for ValidatesTargetsContract
            return "illegal-target"
"""
from __future__ import annotations

from pathlib import Path

import pytest

from read_config_core.base import ConfigBackend
from read_config_core.filesystem import FilesystemBackend
from read_config_core.kv import InMemoryKVClient, KVBackend

sqlalchemy = pytest.importorskip("sqlalchemy")

from read_config_core.sql import SQLBackend  # noqa: E402

fakeredis = pytest.importorskip("fakeredis")

from read_config_core.kv_redis import RedisKVClient  # noqa: E402

requests_mock_pkg = pytest.importorskip("requests_mock")

from read_config_core.http import HTTPBackend  # noqa: E402


class BackendContract:
    """Invariants every ``ConfigBackend`` must honor."""

    ROLE_NAME = "testrole"

    # --- subclasses MUST override ---------------------------------------
    @pytest.fixture
    def backend(self):  # pragma: no cover - abstract
        raise NotImplementedError("override in subclass")

    @pytest.fixture
    def populated_location(self, backend):  # pragma: no cover - abstract
        raise NotImplementedError("override in subclass")

    @pytest.fixture
    def empty_location(self, backend):  # pragma: no cover - abstract
        raise NotImplementedError("override in subclass")

    # --- protocol conformance ------------------------------------------
    def test_satisfies_protocol(self, backend) -> None:
        assert isinstance(backend, ConfigBackend)

    # --- discover -------------------------------------------------------
    def test_discover_is_iterable_of_strings(self, backend) -> None:
        result = list(backend.discover(self.ROLE_NAME))
        assert all(isinstance(loc, str) for loc in result)

    # --- resolve_ancestry ----------------------------------------------
    def test_resolve_ancestry_returns_non_empty_list(
        self, backend, populated_location: str
    ) -> None:
        chain = backend.resolve_ancestry(populated_location)
        assert isinstance(chain, list)
        assert len(chain) >= 1

    def test_resolve_ancestry_ends_with_target(
        self, backend, populated_location: str
    ) -> None:
        chain = backend.resolve_ancestry(populated_location)
        # Contract: the target itself is the last element of the chain.
        assert chain[-1] == populated_location

    def test_resolve_ancestry_elements_are_strings(
        self, backend, populated_location: str
    ) -> None:
        chain = backend.resolve_ancestry(populated_location)
        assert all(isinstance(loc, str) for loc in chain)

    # --- load ----------------------------------------------------------
    def test_load_returns_dict_for_populated_location(
        self, backend, populated_location: str
    ) -> None:
        result = backend.load(populated_location, self.ROLE_NAME)
        assert isinstance(result, dict)

    def test_load_returns_none_for_empty_location(
        self, backend, empty_location: str
    ) -> None:
        assert backend.load(empty_location, self.ROLE_NAME) is None

    # --- exists vs load consistency ------------------------------------
    def test_exists_true_for_populated_location(
        self, backend, populated_location: str
    ) -> None:
        assert backend.exists(populated_location, self.ROLE_NAME) is True

    def test_exists_false_for_empty_location(
        self, backend, empty_location: str
    ) -> None:
        assert backend.exists(empty_location, self.ROLE_NAME) is False

    def test_exists_matches_load_presence(
        self, backend, populated_location: str, empty_location: str
    ) -> None:
        for loc in (populated_location, empty_location):
            assert backend.exists(loc, self.ROLE_NAME) == (
                backend.load(loc, self.ROLE_NAME) is not None
            )

    # --- fingerprint ---------------------------------------------------
    def test_fingerprint_is_stable_for_unchanged_data(
        self, backend, populated_location: str
    ) -> None:
        first = backend.fingerprint(populated_location, self.ROLE_NAME)
        second = backend.fingerprint(populated_location, self.ROLE_NAME)
        assert first is not None
        assert first == second

    def test_fingerprint_returns_none_for_empty_location(
        self, backend, empty_location: str
    ) -> None:
        assert backend.fingerprint(empty_location, self.ROLE_NAME) is None

    def test_fingerprint_is_string(self, backend, populated_location: str) -> None:
        fp = backend.fingerprint(populated_location, self.ROLE_NAME)
        assert isinstance(fp, str)
        assert fp  # non-empty

    # --- identify ------------------------------------------------------
    def test_identify_is_non_empty_string(
        self, backend, populated_location: str
    ) -> None:
        ident = backend.identify(populated_location, self.ROLE_NAME)
        assert isinstance(ident, str)
        assert ident

    def test_identify_is_stable(self, backend, populated_location: str) -> None:
        first = backend.identify(populated_location, self.ROLE_NAME)
        second = backend.identify(populated_location, self.ROLE_NAME)
        assert first == second


class ValidatesTargetsContract:
    """Mix in for backends that raise ``ValueError`` on malformed targets."""

    @pytest.fixture
    def invalid_location(self, backend):  # pragma: no cover - abstract
        raise NotImplementedError("override in subclass")

    def test_resolve_ancestry_rejects_invalid_target(
        self, backend, invalid_location
    ) -> None:
        with pytest.raises(ValueError):
            backend.resolve_ancestry(invalid_location)


class ContentAwareDiscoveryContract:
    """Mix in for backends whose ``discover()`` examines stored data.

    These backends return an empty iterable for a role that has no data at
    all. Structural backends (e.g. HTTP, where the layer list is fixed at
    construction time) do not satisfy this invariant and should not include
    this mixin.
    """

    def test_discover_handles_unknown_role(self, backend) -> None:
        result = list(backend.discover("definitely_not_a_real_role_xyz"))
        assert result == []


# --- FilesystemBackend instantiation ---------------------------------------
class TestFilesystemBackendContract(
    BackendContract, ValidatesTargetsContract, ContentAwareDiscoveryContract
):
    @pytest.fixture
    def backend(self, tmp_path: Path) -> FilesystemBackend:
        root = tmp_path / "cfg"
        (root / "sub").mkdir(parents=True)
        (root / "testrole.yaml").write_text("k: base\n", encoding="utf-8")
        (root / "sub" / "testrole.yaml").write_text("k: child\n", encoding="utf-8")
        (root / "bare").mkdir()
        return FilesystemBackend(str(root))

    @pytest.fixture
    def populated_location(self, backend: FilesystemBackend) -> str:
        return str(Path(backend.root) / "sub")

    @pytest.fixture
    def empty_location(self, backend: FilesystemBackend) -> str:
        return str(Path(backend.root) / "bare")

    @pytest.fixture
    def invalid_location(self, backend: FilesystemBackend) -> str:
        return str(Path(backend.root).parent)  # outside root => traversal


# --- SQLBackend instantiation ----------------------------------------------
class TestSQLBackendContract(BackendContract, ContentAwareDiscoveryContract):
    @pytest.fixture
    def backend(self, tmp_path: Path) -> SQLBackend:
        db_path = tmp_path / "contract.sqlite"
        backend = SQLBackend(dsn=f"sqlite:///{db_path}")
        with backend._engine.begin() as conn:
            conn.execute(
                sqlalchemy.text(
                    """
                    CREATE TABLE role_configs (
                        role_name TEXT NOT NULL,
                        location  TEXT NOT NULL,
                        data      TEXT NOT NULL,
                        PRIMARY KEY (role_name, location)
                    )
                    """
                )
            )
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO role_configs (role_name, location, data) "
                    "VALUES (:r, :l, :d)"
                ),
                {"r": self.ROLE_NAME, "l": "production", "d": '{"k": "v"}'},
            )
        return backend

    @pytest.fixture
    def populated_location(self, backend: SQLBackend) -> str:
        return "production"

    @pytest.fixture
    def empty_location(self, backend: SQLBackend) -> str:
        return "staging-no-row"


# --- In-memory KVBackend instantiation -------------------------------------
class TestInMemoryKVBackendContract(BackendContract, ContentAwareDiscoveryContract):
    @pytest.fixture
    def backend(self) -> KVBackend:
        client = InMemoryKVClient(
            {f"{self.ROLE_NAME}/production": b'{"k": "v"}'}
        )
        return KVBackend(client)

    @pytest.fixture
    def populated_location(self, backend: KVBackend) -> str:
        return "production"

    @pytest.fixture
    def empty_location(self, backend: KVBackend) -> str:
        return "staging-no-row"


# --- Redis KVBackend instantiation (fakeredis) -----------------------------
class TestRedisKVBackendContract(BackendContract, ContentAwareDiscoveryContract):
    @pytest.fixture
    def backend(self) -> KVBackend:
        fake = fakeredis.FakeRedis()
        fake.set(f"{self.ROLE_NAME}/production", b'{"k": "v"}')
        return KVBackend(RedisKVClient(fake))

    @pytest.fixture
    def populated_location(self, backend: KVBackend) -> str:
        return "production"

    @pytest.fixture
    def empty_location(self, backend: KVBackend) -> str:
        return "staging-no-row"


# --- HTTPBackend instantiation (requests-mock) -----------------------------
class TestHTTPBackendContract(BackendContract, ValidatesTargetsContract):
    """HTTP discovery is structural (layer list), not content-aware — so this
    subclass does not include ``ContentAwareDiscoveryContract``. Targets are
    validated (unknown layer name → ValueError), so the targets mixin applies.
    """

    BASE_URL = "https://api.example.com"

    @pytest.fixture
    def backend(self, requests_mock) -> HTTPBackend:
        requests_mock.get(
            f"{self.BASE_URL}/populated",
            json={"k": "v"},
            headers={"ETag": '"stable-etag"'},
        )
        requests_mock.get(
            f"{self.BASE_URL}/empty",
            status_code=404,
        )
        return HTTPBackend(
            layers=[
                {"name": "production", "url": f"{self.BASE_URL}/populated"},
                {"name": "staging-no-row", "url": f"{self.BASE_URL}/empty"},
            ]
        )

    @pytest.fixture
    def populated_location(self, backend: HTTPBackend) -> str:
        return "production"

    @pytest.fixture
    def empty_location(self, backend: HTTPBackend) -> str:
        return "staging-no-row"

    @pytest.fixture
    def invalid_location(self, backend: HTTPBackend) -> str:
        return "layer-that-doesnt-exist"
