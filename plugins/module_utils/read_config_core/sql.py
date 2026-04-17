"""SQL-backed implementation of the ConfigBackend protocol.

Storage model (user-supplied table, schema fully configurable)::

    CREATE TABLE role_configs (
        role_name   TEXT NOT NULL,
        location    TEXT NOT NULL,   -- e.g. "production/web/frontend"
        data        TEXT NOT NULL,   -- JSON payload
        updated_at  TIMESTAMP,
        PRIMARY KEY (role_name, location)
    );

Hierarchy: ``location`` is a ``separator``-delimited path (default ``/``).
Ancestors of ``production/web/frontend`` are ``production`` and
``production/web``. The path itself is included as the tail of the chain.

Dependency: SQLAlchemy is imported lazily so filesystem-only users don't need
it installed.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, kind: str) -> None:
    """Guard against SQL injection via table/column names from config."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {kind}: {name!r}")


class SQLBackend:
    """Read role configs from a relational table keyed by (role_name, location)."""

    def __init__(
        self,
        dsn: str,
        table: str = "role_configs",
        role_column: str = "role_name",
        location_column: str = "location",
        data_column: str = "data",
        separator: str = "/",
    ) -> None:
        try:
            import sqlalchemy
        except ImportError as exc:  # pragma: no cover - exercised only without dep
            raise ImportError(
                "SQLBackend requires SQLAlchemy; install with 'pip install sqlalchemy'."
            ) from exc

        for name, kind in (
            (table, "table"),
            (role_column, "role_column"),
            (location_column, "location_column"),
            (data_column, "data_column"),
        ):
            _validate_identifier(name, kind)

        if not separator:
            raise ValueError("separator must be non-empty")

        self._engine = sqlalchemy.create_engine(dsn)
        self._table = table
        self._role_column = role_column
        self._location_column = location_column
        self._data_column = data_column
        self._separator = separator
        self._sqlalchemy = sqlalchemy

    @property
    def dsn(self) -> str:
        """Return the DSN with the password redacted.

        ``str(engine.url)`` embeds the plaintext password, which is a leak
        risk any time this property is surfaced in a log or error message.
        SQLAlchemy exposes ``URL.render_as_string(hide_password=True)`` for
        the redacted form; fall back to the legacy stringification if that
        method is ever unavailable.
        """
        url = self._engine.url
        render = getattr(url, "render_as_string", None)
        if callable(render):
            return render(hide_password=True)
        return str(url)  # pragma: no cover - legacy SQLAlchemy

    @property
    def table(self) -> str:
        return self._table

    @property
    def separator(self) -> str:
        return self._separator

    def discover(self, role_name: str) -> Iterable[str]:
        stmt = self._sqlalchemy.text(
            f"SELECT {self._location_column} FROM {self._table} "
            f"WHERE {self._role_column} = :role"
        )
        with self._engine.connect() as conn:
            return [row[0] for row in conn.execute(stmt, {"role": role_name})]

    def resolve_ancestry(self, target: str) -> list[str]:
        if not target:
            return [""]
        parts = [p for p in target.split(self._separator) if p != ""]
        chain: list[str] = []
        current = ""
        for part in parts:
            current = f"{current}{self._separator}{part}" if current else part
            chain.append(current)
        return chain

    def load(self, location: str, role_name: str) -> dict | None:
        row = self._fetch_data(location, role_name)
        if row is None:
            return None
        return json.loads(row)

    def exists(self, location: str, role_name: str) -> bool:
        stmt = self._sqlalchemy.text(
            f"SELECT 1 FROM {self._table} "
            f"WHERE {self._role_column} = :role AND {self._location_column} = :loc "
            f"LIMIT 1"
        )
        with self._engine.connect() as conn:
            return (
                conn.execute(stmt, {"role": role_name, "loc": location}).fetchone()
                is not None
            )

    def fingerprint(self, location: str, role_name: str) -> str | None:
        raw = self._fetch_data(location, role_name)
        if raw is None:
            return None
        # Normalize to a canonical string so equivalent JSON always hashes the same.
        parsed = json.loads(raw)
        canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def identify(self, location: str, role_name: str) -> str:
        return f"sql://{self._table}/{role_name}/{location}"

    def _fetch_data(self, location: str, role_name: str) -> str | None:
        stmt = self._sqlalchemy.text(
            f"SELECT {self._data_column} FROM {self._table} "
            f"WHERE {self._role_column} = :role AND {self._location_column} = :loc"
        )
        with self._engine.connect() as conn:
            row = conn.execute(stmt, {"role": role_name, "loc": location}).fetchone()
        return row[0] if row is not None else None
