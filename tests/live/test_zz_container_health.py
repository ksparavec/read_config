"""Container health and log monitoring.

Named ``test_zz_*`` so it collects last: the log assertions are most useful
after the backend tests have actually exercised the services. The session-scoped
guard in conftest is the backstop; these are the explicit, readable checks.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import PODMAN, PREFIX, SERVICES, LogScanner

pytestmark = pytest.mark.live


def inspect(service: str) -> dict:
    proc = subprocess.run(
        [PODMAN, "inspect", f"{PREFIX}-{service}"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(proc.stdout)[0]


@pytest.mark.parametrize("service", SERVICES)
def test_container_is_still_running(service: str) -> None:
    """Nothing crashed or restarted during the run."""
    state = inspect(service)["State"]

    assert state["Running"] is True, f"{service} is {state['Status']}"
    assert state.get("OOMKilled") is not True, f"{service} was OOM-killed"


@pytest.mark.parametrize("service", SERVICES)
def test_container_did_not_restart(service: str) -> None:
    """A restart mid-run means the service died and came back."""
    assert inspect(service)["RestartCount"] == 0, f"{service} restarted"


@pytest.mark.parametrize("service", SERVICES)
def test_container_log_is_free_of_errors(service: str, log_scanner: LogScanner) -> None:
    """No FATAL/PANIC/ERROR lines beyond the declared-benign set."""
    errors = log_scanner.lines(service)

    assert not errors, f"{service} logged {len(errors)} error line(s):\n" + "\n".join(
        errors[:20]
    )


def test_no_backend_was_silently_skipped() -> None:
    """Every client library must be importable.

    The per-store fixtures use ``importorskip``, so a missing driver would make
    that backend vanish from the run while the suite still reported green. This
    turns that into a visible failure.
    """
    required = {
        "sqlalchemy": "sql backend",
        "psycopg": "postgres driver",
        "pymysql": "mariadb driver",
        "redis": "redis backend",
        "etcd3": "etcd backend",
        "consul": "consul backend",
        "requests": "api backend",
    }

    missing = []
    for module, purpose in required.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(f"{module} ({purpose})")

    assert not missing, (
        "missing client libraries, so those backends were skipped: "
        + ", ".join(missing)
        + ". Install with: pip install -r requirements-live.txt"
    )


def test_http_fixture_server_saw_traffic(log_scanner: LogScanner) -> None:
    """nginx logs every request, so an empty log means the api tests never ran."""
    proc = subprocess.run(
        [PODMAN, "logs", "--since", log_scanner.since, f"{PREFIX}-nginx"],
        capture_output=True,
        text=True,
    )

    assert ((proc.stdout or "") + (proc.stderr or "")).strip(), (
        "nginx saw no requests this session"
    )
