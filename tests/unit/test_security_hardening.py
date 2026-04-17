"""Unit tests covering the security hardenings added in 1.0.1.

Covers:
* ``no_log=True`` on the ``backend_options`` argument spec.
* ``track_changes=True`` rejection when the active backend isn't filesystem.
* HTTP context sanitization (format-string gadget rejection).
* HTTP ``allowed_hosts`` gate on outbound requests.
* ``SQLBackend.dsn`` password redaction.
* ``validate_against_schema`` rejects non-regular-file paths.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from read_config_core.http import HTTPBackend


# --- no_log on backend_options ---------------------------------------------
def test_backend_options_argument_spec_has_no_log(read_config_module) -> None:
    """Introspect run_module to assert backend_options carries no_log=True.

    We can't rely on grepping the source (brittle); instead, call the real
    AnsibleModule argument-spec machinery with a backend_options dict and
    verify the parameter is marked no_log. The ``_VALID_ARGS`` attribute on
    ``AnsibleModule`` doesn't expose no_log, so we inspect the argument_spec
    definition directly by reading the dict from the module source via a
    lightweight AST walk.
    """
    import ast

    source = Path(read_config_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    found = False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "dict"
        ):
            # Look for backend_options=dict(..., no_log=True, ...). We
            # identify the right call by searching kwargs for no_log + a
            # sibling ``type="dict"`` kwarg (argument spec shape).
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            if "no_log" in kwargs and kwargs.get("type") and isinstance(
                kwargs["type"], ast.Constant
            ) and kwargs["type"].value == "dict":
                no_log = kwargs["no_log"]
                assert isinstance(no_log, ast.Constant) and no_log.value is True
                found = True
    assert found, "backend_options argument_spec must declare no_log=True"


# --- track_changes + non-filesystem backend --------------------------------
def test_track_changes_rejected_for_non_filesystem_backend(
    run_module_args, tmp_path: Path
) -> None:
    """Regression test for the guard in read_config.run_module()."""
    result = run_module_args(
        {
            "role_name": "testrole",
            "backend": "redis",
            "backend_options": {"url": "redis://localhost:6379/0"},
            "track_changes": True,
        }
    )

    assert result["ok"] is False
    assert "track_changes is only supported for the filesystem backend" in (
        result["result"]["msg"]
    )


# --- HTTP context sanitization ---------------------------------------------
def test_http_backend_rejects_format_gadget_in_context() -> None:
    with pytest.raises(ValueError, match=r"must not contain '\{' or '\}'"):
        HTTPBackend(
            layers=[{"name": "x", "url": "https://example.com/x"}],
            context={"evil": "{0.__class__.__mro__}"},
        )


def test_http_backend_rejects_closing_brace_in_context() -> None:
    with pytest.raises(ValueError, match=r"must not contain"):
        HTTPBackend(
            layers=[{"name": "x", "url": "https://example.com/x"}],
            context={"nested": "value}"},
        )


def test_http_backend_accepts_non_string_context() -> None:
    # Ints, bools, etc. don't carry format tokens themselves — accept them.
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://example.com/x/{host_id}"}],
        context={"host_id": 42},
    )

    assert backend.context == {"host_id": 42}


def test_http_backend_accepts_plain_strings_in_context() -> None:
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://example.com/{org}/x"}],
        context={"org": "acme-corp"},
    )

    assert backend.context == {"org": "acme-corp"}


# --- HTTP allowed_hosts gate ------------------------------------------------
def test_http_allowed_hosts_blocks_external_host(requests_mock) -> None:
    """An URL that resolves outside the allowlist must fail before the call."""
    requests_mock.get("https://evil.example.com/x", json={"k": "v"})
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://evil.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    with pytest.raises(ValueError, match="not in allowed_hosts"):
        backend.load("x", "myrole")

    assert requests_mock.call_count == 0  # no request should have been issued


def test_http_allowed_hosts_permits_listed_host(requests_mock) -> None:
    requests_mock.get("https://api.example.com/x", json={"k": "v"})
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://api.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_allowed_hosts_case_insensitive(requests_mock) -> None:
    requests_mock.get("https://API.example.com/x", json={"k": "v"})
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://API.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_no_allowlist_permits_all_hosts(requests_mock) -> None:
    """Backwards compatibility: no allowlist means no restriction."""
    requests_mock.get("https://anywhere.example.net/x", json={"k": "v"})
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://anywhere.example.net/x"}],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_allowed_hosts_exposed_as_property() -> None:
    backend = HTTPBackend(
        layers=[{"name": "x", "url": "https://api.example.com/x"}],
        allowed_hosts=["api.example.com", "api-staging.example.com"],
    )

    assert backend.allowed_hosts == frozenset(
        {"api.example.com", "api-staging.example.com"}
    )


# --- SQL DSN redaction ------------------------------------------------------
def test_sql_dsn_hides_password() -> None:
    pytest.importorskip("sqlalchemy")
    from read_config_core.sql import SQLBackend

    backend = SQLBackend(
        dsn="sqlite:///:memory:",  # passwordless DSN still exercises the property
    )

    # No password to redact — just confirms the call shape works.
    assert backend.dsn.startswith("sqlite:///")


def test_sql_dsn_redacts_password_from_url() -> None:
    """The ``dsn`` property must use SQLAlchemy's password-hiding renderer.

    We can't pass a driver-bound DSN (postgres, mysql) without the driver
    installed, so swap the engine for a stand-in whose ``url`` is a real
    SQLAlchemy ``URL`` object with a password — then assert the property
    returns the hidden form.
    """
    sqlalchemy = pytest.importorskip("sqlalchemy")
    from read_config_core.sql import SQLBackend

    backend = SQLBackend(dsn="sqlite:///:memory:")
    backend._engine = type(
        "FakeEngine",
        (),
        {
            "url": sqlalchemy.engine.url.make_url(
                "postgresql+psycopg://user:superSecret123@db.example.com/mydb"
            )
        },
    )()

    rendered = backend.dsn
    assert "superSecret123" not in rendered
    assert "user" in rendered  # username is not redacted
    assert "db.example.com" in rendered


# --- validate_schema path containment --------------------------------------
def test_validate_schema_rejects_directory(tmp_path: Path, read_config_module) -> None:
    with pytest.raises(ValueError, match="not a regular file"):
        read_config_module.validate_against_schema({}, str(tmp_path))


def test_validate_schema_rejects_fifo(tmp_path: Path, read_config_module) -> None:
    fifo = tmp_path / "pipe"
    try:
        os.mkfifo(str(fifo))
    except (AttributeError, OSError):  # pragma: no cover - non-POSIX
        pytest.skip("mkfifo unavailable on this platform")

    with pytest.raises(ValueError, match="not a regular file"):
        read_config_module.validate_against_schema({}, str(fifo))


def test_validate_schema_accepts_regular_file(
    tmp_path: Path, schema_file: Path, read_config_module
) -> None:
    assert (
        read_config_module.validate_against_schema(
            {"key1": "hi", "key2": {}}, str(schema_file)
        )
        is True
    )
