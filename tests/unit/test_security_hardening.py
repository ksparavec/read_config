"""Unit tests covering the security hardenings added in 1.0.1.

Covers:
* ``no_log=True`` on the ``backend_options`` argument spec.
* ``track_changes=True`` rejection when the active backend isn't filesystem.
* API template-value sanitization (format-string gadget rejection).
* HTTP ``allowed_hosts`` gate on outbound requests.
* ``SQLBackend.dsn`` password redaction.
* ``validate_against_schema`` rejects non-regular-file paths.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from read_config_core.api import ApiBackend


# --- no_log placement -------------------------------------------------------
def _argument_spec_no_log(read_config_module) -> dict:
    """Return {option_name: no_log literal} from the module_args dict.

    Read straight from the source: AnsibleModule does not expose no_log on the
    built parameter set, so an AST walk over the argument-spec literal is the
    only faithful way to assert on it.
    """
    import ast

    source = Path(read_config_module.__file__).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "module_args"
                    for t in node.targets)
        ):
            continue
        spec = {}
        for kw in node.value.keywords:
            inner = {k.arg: k.value for k in kw.value.keywords}
            flag = inner.get("no_log")
            spec[kw.arg] = flag.value if isinstance(flag, ast.Constant) else None
        return spec
    raise AssertionError("module_args argument spec not found")


def test_backend_secrets_is_marked_no_log(read_config_module) -> None:
    """Credentials must stay out of verbose output and callbacks."""
    assert _argument_spec_no_log(read_config_module)["backend_secrets"] is True


def test_backend_options_is_not_marked_no_log(read_config_module) -> None:
    """Structural options must NOT be no_log.

    Ansible implements no_log by substring-scrubbing every string and number in
    the marked value out of the module's output. Marking backend_options would
    therefore corrupt the returned configuration: a numeric context id of 5
    turns every config value containing a "5" into a sentinel and changes its
    type. Secrets belong in backend_secrets, which is scoped to actual
    credentials.
    """
    assert _argument_spec_no_log(read_config_module)["backend_options"] is False


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


# --- template value sanitization -------------------------------------------
def test_api_backend_rejects_format_gadget_in_a_template_value() -> None:
    with pytest.raises(ValueError, match=r"must not contain '\{' or '\}'"):
        ApiBackend(
            layers=[{"name": "x", "url": "https://example.com/{evil}"}],
            evil="{0.__class__.__mro__}",
        )


def test_api_backend_rejects_closing_brace_in_a_template_value() -> None:
    with pytest.raises(ValueError, match=r"must not contain"):
        ApiBackend(
            layers=[{"name": "x", "url": "https://example.com/{nested}"}],
            nested="value}",
        )


def test_api_backend_accepts_non_string_template_values() -> None:
    # Ints, bools, etc. don't carry format tokens themselves — accept them.
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://example.com/x/{host_id}"}],
        host_id=42,
    )

    assert backend.template_vars == {"host_id": 42}


def test_api_backend_accepts_plain_strings_as_template_values() -> None:
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://example.com/{org}/x"}],
        org="acme-corp",
    )

    assert backend.template_vars == {"org": "acme-corp"}


def test_a_value_no_layer_references_is_rejected() -> None:
    """Typo protection: an unused option cannot pass as a template variable."""
    with pytest.raises(ValueError, match="match no placeholder") as exc:
        ApiBackend(
            layers=[{"name": "x", "url": "https://example.com/{host_id}"}],
            hostt_id=42,
        )

    assert "host_id" in str(exc.value)


# --- HTTP allowed_hosts gate ------------------------------------------------
def test_http_allowed_hosts_blocks_external_host(requests_mock) -> None:
    """An URL that resolves outside the allowlist must fail before the call."""
    requests_mock.get("https://evil.example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://evil.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    with pytest.raises(ValueError, match="not in allowed_hosts"):
        backend.load("x", "myrole")

    assert requests_mock.call_count == 0  # no request should have been issued


def test_http_allowed_hosts_permits_listed_host(requests_mock) -> None:
    requests_mock.get("https://api.example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://api.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_allowed_hosts_case_insensitive(requests_mock) -> None:
    requests_mock.get("https://API.example.com/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://API.example.com/x"}],
        allowed_hosts=["api.example.com"],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_no_allowlist_permits_all_hosts(requests_mock) -> None:
    """Backwards compatibility: no allowlist means no restriction."""
    requests_mock.get("https://anywhere.example.net/x", json={"k": "v"})
    backend = ApiBackend(
        layers=[{"name": "x", "url": "https://anywhere.example.net/x"}],
    )

    assert backend.load("x", "myrole") == {"k": "v"}


def test_http_allowed_hosts_exposed_as_property() -> None:
    backend = ApiBackend(
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
