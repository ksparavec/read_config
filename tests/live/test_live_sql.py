"""SQL backend against real PostgreSQL and MariaDB servers.

Covers what the in-memory SQLite unit tests cannot: a real driver, a real
network round trip, and two different SQL dialects behind the same
SQLAlchemy DSN.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from read_config_core.base import MergeEngine
from read_config_core.sql import SQLBackend

from .conftest import DEEPEST, EXPECTED_MERGE, HIERARCHY, MYSQL_DSN, PG_DSN, ROLE

pytestmark = pytest.mark.live

DIALECTS = ("postgres", "mariadb")


@pytest.fixture(params=DIALECTS)
def dialect(request, postgres_table, mysql_table):
    """(name, dsn, table) for each live SQL server."""
    if request.param == "postgres":
        return "postgres", PG_DSN, postgres_table
    return "mariadb", MYSQL_DSN, mysql_table


def test_merges_hierarchy_on_both_dialects(dialect) -> None:
    name, dsn, table = dialect

    result = MergeEngine(SQLBackend(dsn=dsn, table=table)).build(DEEPEST, ROLE)

    assert result.data == EXPECTED_MERGE, name


def test_discover_returns_every_seeded_location(dialect) -> None:
    name, dsn, table = dialect

    discovered = set(SQLBackend(dsn=dsn, table=table).discover(ROLE))

    assert discovered == set(HIERARCHY), name


def test_identify_is_a_stable_sql_uri(dialect) -> None:
    name, dsn, table = dialect
    backend = SQLBackend(dsn=dsn, table=table)

    identifier = backend.identify(DEEPEST, ROLE)

    assert identifier == f"sql://{table}/{ROLE}/{DEEPEST}", name
    assert backend.identify(DEEPEST, ROLE) == identifier, name


def test_fingerprint_is_sha256_of_canonical_json(dialect) -> None:
    name, dsn, table = dialect
    backend = SQLBackend(dsn=dsn, table=table)

    canonical = json.dumps(
        HIERARCHY[DEEPEST], sort_keys=True, separators=(",", ":")
    )
    expected = hashlib.sha256(canonical.encode()).hexdigest()

    assert backend.fingerprint(DEEPEST, ROLE) == expected, name


def test_fingerprint_tracks_a_real_update(dialect, sqlalchemy_mod) -> None:
    """Change a row through SQL; the fingerprint must follow it and revert."""
    name, dsn, table = dialect
    backend = SQLBackend(dsn=dsn, table=table)
    original = backend.fingerprint(DEEPEST, ROLE)

    engine = sqlalchemy_mod.create_engine(dsn)
    changed = dict(HIERARCHY[DEEPEST], workers=999)
    try:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy_mod.text(
                    f"UPDATE {table} SET data = :d "
                    "WHERE role_name = :r AND location = :l"
                ),
                {"d": json.dumps(changed), "r": ROLE, "l": DEEPEST},
            )
        assert backend.fingerprint(DEEPEST, ROLE) != original, name
        assert MergeEngine(backend).build(DEEPEST, ROLE).data["workers"] == 999
    finally:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy_mod.text(
                    f"UPDATE {table} SET data = :d "
                    "WHERE role_name = :r AND location = :l"
                ),
                {"d": json.dumps(HIERARCHY[DEEPEST]), "r": ROLE, "l": DEEPEST},
            )
        engine.dispose()

    assert backend.fingerprint(DEEPEST, ROLE) == original, name


def test_dsn_property_redacts_the_password(dialect) -> None:
    name, dsn, table = dialect

    rendered = SQLBackend(dsn=dsn, table=table).dsn

    assert "rcpass" not in rendered, f"{name}: password leaked in dsn property"
    assert "***" in rendered, name


def test_custom_column_names_are_honoured(dialect, sqlalchemy_mod) -> None:
    """The role/location/data column names are configurable, not fixed."""
    name, dsn, _table = dialect
    table = "custom_cols"
    engine = sqlalchemy_mod.create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.execute(sqlalchemy_mod.text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(
                sqlalchemy_mod.text(
                    f"CREATE TABLE {table} ("
                    "  r VARCHAR(128) NOT NULL,"
                    "  loc VARCHAR(255) NOT NULL,"
                    "  payload TEXT NOT NULL,"
                    "  PRIMARY KEY (r, loc))"
                )
            )
            for location, data in HIERARCHY.items():
                conn.execute(
                    sqlalchemy_mod.text(
                        f"INSERT INTO {table} (r, loc, payload) VALUES (:r,:l,:d)"
                    ),
                    {"r": ROLE, "l": location, "d": json.dumps(data)},
                )

        backend = SQLBackend(
            dsn=dsn,
            table=table,
            role_column="r",
            location_column="loc",
            data_column="payload",
        )

        assert MergeEngine(backend).build(DEEPEST, ROLE).data == EXPECTED_MERGE, name
    finally:
        with engine.begin() as conn:
            conn.execute(sqlalchemy_mod.text(f"DROP TABLE IF EXISTS {table}"))
        engine.dispose()


def test_custom_separator_changes_the_ancestry_split(dialect, sqlalchemy_mod) -> None:
    """A non-'/' separator re-segments the location path."""
    name, dsn, _table = dialect
    table = "dotted_sep"
    engine = sqlalchemy_mod.create_engine(dsn)
    dotted = {k.replace("/", "."): v for k, v in HIERARCHY.items()}
    try:
        with engine.begin() as conn:
            conn.execute(sqlalchemy_mod.text(f"DROP TABLE IF EXISTS {table}"))
            conn.execute(
                sqlalchemy_mod.text(
                    f"CREATE TABLE {table} ("
                    "  role_name VARCHAR(128) NOT NULL,"
                    "  location VARCHAR(255) NOT NULL,"
                    "  data TEXT NOT NULL,"
                    "  PRIMARY KEY (role_name, location))"
                )
            )
            for location, data in dotted.items():
                conn.execute(
                    sqlalchemy_mod.text(
                        f"INSERT INTO {table} (role_name, location, data) "
                        "VALUES (:r,:l,:d)"
                    ),
                    {"r": ROLE, "l": location, "d": json.dumps(data)},
                )

        backend = SQLBackend(dsn=dsn, table=table, separator=".")
        target = DEEPEST.replace("/", ".")

        assert backend.resolve_ancestry(target) == [
            "global",
            "global.production",
            "global.production.eu-west",
        ], name
        assert MergeEngine(backend).build(target, ROLE).data == EXPECTED_MERGE, name
    finally:
        with engine.begin() as conn:
            conn.execute(sqlalchemy_mod.text(f"DROP TABLE IF EXISTS {table}"))
        engine.dispose()


@pytest.mark.parametrize("bad", ["role_configs; DROP TABLE x", "a-b", "1abc", ""])
def test_invalid_table_identifiers_are_rejected(dialect, bad) -> None:
    """Identifier validation happens before any SQL is built."""
    _name, dsn, _table = dialect

    with pytest.raises(ValueError, match="Invalid table"):
        SQLBackend(dsn=dsn, table=bad)


def test_role_name_is_a_bound_parameter_not_interpolated(dialect) -> None:
    """A role name full of SQL metacharacters is data, never syntax."""
    name, dsn, table = dialect
    backend = SQLBackend(dsn=dsn, table=table)

    hostile = "'; DROP TABLE role_configs; --"

    assert backend.load(DEEPEST, hostile) is None, name
    # The table is still there and still serving the real role.
    assert MergeEngine(backend).build(DEEPEST, ROLE).data == EXPECTED_MERGE, name
