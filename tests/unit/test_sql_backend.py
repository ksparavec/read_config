"""Unit tests for SQLBackend against an in-memory SQLite database."""
from __future__ import annotations

import json
from typing import Any

import pytest

sqlalchemy = pytest.importorskip("sqlalchemy")

from read_config_core.base import MergeEngine
from read_config_core.sql import SQLBackend


def _install_schema(engine: Any, rows: list[dict]) -> None:
    """Create the role_configs table on ``engine`` and seed it with ``rows``."""
    with engine.begin() as conn:
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
        for row in rows:
            conn.execute(
                sqlalchemy.text(
                    "INSERT INTO role_configs (role_name, location, data) "
                    "VALUES (:role, :loc, :data)"
                ),
                {
                    "role": row["role_name"],
                    "loc": row["location"],
                    "data": json.dumps(row["data"]),
                },
            )


@pytest.fixture
def backend_with_rows(tmp_path):
    """Return a ``(backend, engine)`` pair with seeded data.

    Each test gets its own SQLite file under ``tmp_path`` for isolation — no
    cross-test pollution from a shared in-memory cache.
    """
    db_path = tmp_path / "test.sqlite"

    def _factory(rows: list[dict]) -> tuple[SQLBackend, Any]:
        backend = SQLBackend(dsn=f"sqlite:///{db_path}")
        _install_schema(backend._engine, rows)
        return backend, backend._engine

    return _factory


def test_resolve_ancestry_path_segments() -> None:
    backend = SQLBackend(dsn="sqlite:///:memory:")

    assert backend.resolve_ancestry("production/web/frontend") == [
        "production",
        "production/web",
        "production/web/frontend",
    ]


def test_resolve_ancestry_single_segment() -> None:
    backend = SQLBackend(dsn="sqlite:///:memory:")

    assert backend.resolve_ancestry("production") == ["production"]


def test_resolve_ancestry_empty_target() -> None:
    backend = SQLBackend(dsn="sqlite:///:memory:")

    assert backend.resolve_ancestry("") == [""]


def test_resolve_ancestry_strips_empty_segments() -> None:
    backend = SQLBackend(dsn="sqlite:///:memory:")

    # Leading/trailing/double slashes should not create empty chain entries.
    assert backend.resolve_ancestry("/production//web/") == [
        "production",
        "production/web",
    ]


def test_resolve_ancestry_custom_separator() -> None:
    backend = SQLBackend(dsn="sqlite:///:memory:", separator=".")

    assert backend.resolve_ancestry("prod.web.frontend") == [
        "prod",
        "prod.web",
        "prod.web.frontend",
    ]


def test_load_returns_data_for_known_location(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [
            {"role_name": "r", "location": "production", "data": {"k1": "v1"}},
        ]
    )

    assert backend.load("production", "r") == {"k1": "v1"}


def test_load_returns_none_for_missing_location(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"k1": "v1"}}]
    )

    assert backend.load("staging", "r") is None


def test_load_ignores_other_roles(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [
            {"role_name": "r", "location": "production", "data": {"k1": "v1"}},
            {"role_name": "other", "location": "production", "data": {"k2": "v2"}},
        ]
    )

    assert backend.load("production", "r") == {"k1": "v1"}


def test_exists_true_when_row_present(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"k": 1}}]
    )

    assert backend.exists("production", "r") is True


def test_exists_false_when_row_absent(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"k": 1}}]
    )

    assert backend.exists("staging", "r") is False


def test_discover_returns_all_locations_for_role(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [
            {"role_name": "r", "location": "production", "data": {}},
            {"role_name": "r", "location": "staging", "data": {}},
            {"role_name": "other", "location": "production", "data": {}},
        ]
    )

    assert set(backend.discover("r")) == {"production", "staging"}


def test_discover_empty_for_unknown_role(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {}}]
    )

    assert list(backend.discover("ghost")) == []


def test_fingerprint_stable_for_unchanged_row(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"a": 1, "b": 2}}]
    )

    assert backend.fingerprint("production", "r") == backend.fingerprint(
        "production", "r"
    )


def test_fingerprint_changes_when_data_changes(backend_with_rows) -> None:
    backend, engine = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"a": 1}}]
    )
    first = backend.fingerprint("production", "r")

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                "UPDATE role_configs SET data = :d "
                "WHERE role_name = :r AND location = :l"
            ),
            {"d": json.dumps({"a": 2}), "r": "r", "l": "production"},
        )
    second = backend.fingerprint("production", "r")

    assert first != second


def test_fingerprint_returns_none_for_missing(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [{"role_name": "r", "location": "production", "data": {"a": 1}}]
    )

    assert backend.fingerprint("ghost", "r") is None


def test_fingerprint_ignores_key_order(backend_with_rows) -> None:
    """Equivalent JSON with different key order must fingerprint to the same hash."""
    backend, engine = backend_with_rows(
        [{"role_name": "r", "location": "p", "data": {"a": 1, "b": 2}}]
    )
    canonical = backend.fingerprint("p", "r")

    with engine.begin() as conn:
        conn.execute(
            sqlalchemy.text(
                "UPDATE role_configs SET data = :d "
                "WHERE role_name = :r AND location = :l"
            ),
            {"d": '{"b": 2, "a": 1}', "r": "r", "l": "p"},
        )

    assert backend.fingerprint("p", "r") == canonical


def test_identify_returns_stable_uri(backend_with_rows) -> None:
    backend, _ = backend_with_rows([])

    assert (
        backend.identify("production/web", "r")
        == "sql://role_configs/r/production/web"
    )


def test_rejects_invalid_table_name() -> None:
    with pytest.raises(ValueError, match="Invalid table"):
        SQLBackend(dsn="sqlite:///:memory:", table="role_configs; DROP TABLE users;")


def test_rejects_invalid_column_names() -> None:
    with pytest.raises(ValueError, match="Invalid role_column"):
        SQLBackend(dsn="sqlite:///:memory:", role_column="role--name")


def test_rejects_empty_separator() -> None:
    with pytest.raises(ValueError, match="separator must be non-empty"):
        SQLBackend(dsn="sqlite:///:memory:", separator="")


def test_engine_end_to_end_merges_hierarchy(backend_with_rows) -> None:
    backend, _ = backend_with_rows(
        [
            {"role_name": "r", "location": "production", "data": {"k1": "base", "shared": "prod"}},
            {"role_name": "r", "location": "production/web", "data": {"k2": "web", "shared": "prod_web"}},
            {"role_name": "r", "location": "production/web/frontend", "data": {"k3": "fe"}},
        ]
    )
    engine = MergeEngine(backend)

    result = engine.build("production/web/frontend", "r")

    assert result.data == {
        "k1": "base",
        "k2": "web",
        "k3": "fe",
        "shared": "prod_web",
    }
    assert result.sources == [
        "sql://role_configs/r/production",
        "sql://role_configs/r/production/web",
        "sql://role_configs/r/production/web/frontend",
    ]


def test_properties_expose_configuration() -> None:
    backend = SQLBackend(
        dsn="sqlite:///:memory:", table="my_configs", separator="."
    )

    assert backend.table == "my_configs"
    assert backend.separator == "."
    assert backend.dsn.startswith("sqlite:///")
