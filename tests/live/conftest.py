"""Session fixtures for the live backend suite.

The containers started by ``containers.sh`` are servers only. Everything here
runs on the host: it seeds each store through its normal client library, hands
the test a real ``ConfigBackend``, and tears the data back down.

Design notes:

* All fixtures are **session-scoped**. Containers are started once by
  ``make live-up`` and reused for the whole run; nothing here starts or stops
  a container, so no test pays container startup cost.
* Every backend is seeded with the *same* canonical hierarchy
  (:data:`HIERARCHY`) and asserted against the *same* expected merge
  (:data:`EXPECTED_MERGE`). That is the point of the suite: the storage
  backend is pluggable, the merge semantics are not.
* Container logs are scanned for error lines at session teardown, scoped to
  the test window so startup noise is excluded.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CONTAINERS_SH = HERE / "containers.sh"
PODMAN = os.environ.get("PODMAN", "podman")
PREFIX = os.environ.get("RC_LIVE_PREFIX", "rclive")

ROLE = "webapp"

# Endpoints must match the port table in containers.sh.
PG_DSN = "postgresql+psycopg://rcuser:rcpass@127.0.0.1:15432/rcdb"
MYSQL_DSN = "mysql+pymysql://rcuser:rcpass@127.0.0.1:13306/rcdb"
REDIS_URL = "redis://:rcpass@127.0.0.1:16379/0"
ETCD_HOST, ETCD_PORT = "127.0.0.1", 12379
CONSUL_HOST, CONSUL_PORT = "127.0.0.1", 18500
HTTP_BASE = "http://127.0.0.1:18080"
HTTP_TOKEN = "rc-test-token"

# A real Foreman 3.16.0, seeded by tests/live/foreman/seed.sh. The api
# backend's foreman adapter is written against this product's API, so it is
# tested against the product rather than a fixture of it.
FOREMAN_BASE = "http://127.0.0.1:13000"
FOREMAN_AUTH = ["admin", "changeme"]
FOREMAN_HOST = "web01.eu.example.com"

# Real NetBox 4.6.8. Its v2 API tokens are presented as
# "Bearer nbt_<key>.<token>", which is what the adapter sends as auth_token.
NETBOX_BASE = "http://127.0.0.1:18000"
NETBOX_TOKEN = "nbt_rcliveKey123.0123456789abcdef0123456789abcdef01234567"
NETBOX_HOST = "web01"

# Real AWX 24.6.1, served by Django directly (its bundled nginx wants port 80).
AWX_BASE = "http://127.0.0.1:18052"
AWX_AUTH = ["admin", "changeme"]
AWX_HOST = "web01"

SERVICES = (
    "postgres", "mariadb", "redis", "etcd", "consul", "nginx",
    "foreman", "netbox", "awx",
)

# --- the canonical hierarchy every backend is tested against ---------------
#
# Rooted at a named "global" segment rather than at the empty location on
# purpose: SQL and KV ancestry chains start at the first path segment and have
# no implicit root level, so a config stored at the bare root key would never
# be merged into its descendants. Rooting at "global" is the portable shape.
HIERARCHY: dict[str, dict] = {
    "global": {
        "listen_port": 8080,
        "workers": 2,
        "log_level": "info",
        "database": {"pool_size": 10, "host": "db.default.internal"},
    },
    "global/production": {
        "workers": 8,
        "log_level": "warn",
        "database": {"pool_size": 50},
    },
    "global/production/eu-west": {
        "region": "eu-west",
        "database": {"pool_size": 40, "host": "db.eu-west.internal"},
    },
}

DEEPEST = "global/production/eu-west"

# What merging the full chain must produce, on every backend.
EXPECTED_MERGE: dict = {
    "listen_port": 8080,           # only in global
    "workers": 8,                  # production overrides global
    "log_level": "warn",           # production overrides global
    "region": "eu-west",           # only in eu-west
    "database": {                  # deep-merged across all three levels
        "pool_size": 40,           # eu-west overrides production overrides global
        "host": "db.eu-west.internal",   # eu-west overrides global
    },
}


# --- container plumbing ----------------------------------------------------

def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def _container(service: str) -> str:
    return f"{PREFIX}-{service}"


def _is_running(service: str) -> bool:
    proc = _run(PODMAN, "inspect", "-f", "{{.State.Running}}", _container(service))
    return proc.returncode == 0 and proc.stdout.strip() == "true"


@pytest.fixture(scope="session", autouse=True)
def live_services() -> None:
    """Skip the whole suite unless every backend container is up.

    Set ``RC_LIVE_REQUIRE=1`` to turn the skip into a hard failure (CI).
    """
    missing = [s for s in SERVICES if not _is_running(s)]
    if not missing:
        return
    message = (
        f"live backend containers not running: {', '.join(missing)}. "
        f"Start them with 'make live-up' (or {CONTAINERS_SH} up)."
    )
    if os.environ.get("RC_LIVE_REQUIRE") == "1":
        pytest.fail(message, pytrace=False)
    pytest.skip(message, allow_module_level=True)


class LogScanner:
    """Reads container logs and reports lines that look like real errors."""

    # Kept in sync with ERROR_RE in containers.sh.
    PATTERN = re.compile(
        r"(FATAL|PANIC|CRITICAL|\[ERROR\]|ERROR:|Segmentation fault|OOM"
        r"|out of memory|corrupt)",
        re.IGNORECASE,
    )

    # Benign lines these images emit in normal operation. Anything matched
    # here is not a test failure; everything else that matches PATTERN is.
    BENIGN = (
        # Postgres prints this on every clean first start.
        re.compile(r"database system was shut down", re.I),
        # MariaDB reports the client disconnecting without a COM_QUIT as an
        # aborted connection; SQLAlchemy pooling does this routinely.
        re.compile(r"Aborted connection|got an error reading communication", re.I),
        # Consul dev-mode agent chatter about no ACLs / no TLS.
        re.compile(r"ACL support disabled|Not all enterprise features", re.I),
    )

    def __init__(self, since: str | None) -> None:
        self.since = since
        # Patterns registered by tests that deliberately provoke a server-side
        # error (bad credentials, malformed queries). Without this the guard
        # would flag a test's own intended behaviour as a failure.
        self.expected: list[re.Pattern] = []

    def expect(self, pattern: str) -> None:
        self.expected.append(re.compile(pattern, re.IGNORECASE))

    def lines(self, service: str) -> list[str]:
        args = [PODMAN, "logs"]
        if self.since:
            args += ["--since", self.since]
        args.append(_container(service))
        proc = _run(*args)
        raw = (proc.stdout or "") + (proc.stderr or "")
        hits = [ln for ln in raw.splitlines() if self.PATTERN.search(ln)]
        ignore = list(self.BENIGN) + self.expected
        return [ln for ln in hits if not any(p.search(ln) for p in ignore)]

    def all_errors(self) -> dict[str, list[str]]:
        found = {s: self.lines(s) for s in SERVICES}
        return {s: v for s, v in found.items() if v}


@pytest.fixture
def expect_container_error(log_scanner: "LogScanner"):
    """Let a test declare a container error it intentionally provokes.

    Registered patterns are excluded from the session-end log guard.
    """
    return log_scanner.expect


@pytest.fixture(scope="session")
def log_scanner() -> LogScanner:
    """Scanner scoped to *this* pytest session.

    Containers outlive individual runs, so anchoring to container start would
    make one session inherit the errors of the last one. Anchoring to session
    start also excludes the noisy boot/readiness phase for free.
    """
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return LogScanner(started)


@pytest.fixture(scope="session", autouse=True)
def _container_log_guard(live_services, log_scanner: LogScanner):
    """Backstop: fail the session if any container logged an error."""
    yield
    errors = log_scanner.all_errors()
    if errors:
        report = "\n".join(
            f"--- {svc} ---\n" + "\n".join(lines) for svc, lines in errors.items()
        )
        pytest.fail(f"container logs contain error lines:\n{report}", pytrace=False)


# --- per-backend seeded fixtures -------------------------------------------

@pytest.fixture(scope="session")
def sqlalchemy_mod():
    return pytest.importorskip("sqlalchemy")


def _seed_sql(sqlalchemy, dsn: str, table: str) -> None:
    engine = sqlalchemy.create_engine(dsn)
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text(f"DROP TABLE IF EXISTS {table}"))
        conn.execute(
            sqlalchemy.text(
                f"CREATE TABLE {table} ("
                "  role_name VARCHAR(128) NOT NULL,"
                "  location  VARCHAR(255) NOT NULL,"
                "  data      TEXT NOT NULL,"
                "  PRIMARY KEY (role_name, location))"
            )
        )
        for location, payload in HIERARCHY.items():
            conn.execute(
                sqlalchemy.text(
                    f"INSERT INTO {table} (role_name, location, data) "
                    "VALUES (:r, :l, :d)"
                ),
                {"r": ROLE, "l": location, "d": json.dumps(payload)},
            )
    engine.dispose()


@pytest.fixture(scope="session")
def postgres_table(sqlalchemy_mod) -> str:
    pytest.importorskip("psycopg")
    table = "role_configs"
    _seed_sql(sqlalchemy_mod, PG_DSN, table)
    return table


@pytest.fixture(scope="session")
def mysql_table(sqlalchemy_mod) -> str:
    pytest.importorskip("pymysql")
    table = "role_configs"
    _seed_sql(sqlalchemy_mod, MYSQL_DSN, table)
    return table


@pytest.fixture(scope="session")
def redis_seeded() -> str:
    """Seed Redis under a dedicated prefix and return that prefix."""
    redis = pytest.importorskip("redis")
    prefix = "rclive"
    client = redis.from_url(REDIS_URL)
    for key in client.scan_iter(match=f"{prefix}/{ROLE}/*"):
        client.delete(key)
    for location, payload in HIERARCHY.items():
        client.set(f"{prefix}/{ROLE}/{location}", json.dumps(payload))
    client.close()
    return prefix


@pytest.fixture(scope="session")
def etcd_seeded() -> str:
    etcd3 = pytest.importorskip("etcd3")
    prefix = "rclive"
    client = etcd3.client(host=ETCD_HOST, port=ETCD_PORT)
    client.delete_prefix(f"{prefix}/{ROLE}/")
    for location, payload in HIERARCHY.items():
        client.put(f"{prefix}/{ROLE}/{location}", json.dumps(payload))
    return prefix


@pytest.fixture(scope="session")
def consul_seeded() -> str:
    consul = pytest.importorskip("consul")
    prefix = "rclive"
    client = consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)
    client.kv.delete(f"{prefix}/{ROLE}/", recurse=True)
    for location, payload in HIERARCHY.items():
        client.kv.put(f"{prefix}/{ROLE}/{location}", json.dumps(payload))
    return prefix


# --- one constructed backend per store, for the cross-backend parity test ---
#
# Each entry yields a ready-to-merge backend whose deepest location is
# DEEPEST and whose merged result must equal EXPECTED_MERGE. The API backend
# reaches the same shape through an ordered layer list rather than a path.

def _sql_backend(dsn: str, table: str):
    from read_config_core.sql import SQLBackend

    return SQLBackend(dsn=dsn, table=table)


@pytest.fixture(scope="session")
def backend_postgres(postgres_table):
    return _sql_backend(PG_DSN, postgres_table)


@pytest.fixture(scope="session")
def backend_mariadb(mysql_table):
    return _sql_backend(MYSQL_DSN, mysql_table)


@pytest.fixture(scope="session")
def backend_redis(redis_seeded):
    from read_config_core.kv_redis import make_redis_backend

    return make_redis_backend(url=REDIS_URL, prefix=redis_seeded)


@pytest.fixture(scope="session")
def backend_etcd(etcd_seeded):
    from read_config_core.kv_etcd import make_etcd_backend

    return make_etcd_backend(host=ETCD_HOST, port=ETCD_PORT, prefix=etcd_seeded)


@pytest.fixture(scope="session")
def backend_consul(consul_seeded):
    from read_config_core.kv_consul import make_consul_backend

    return make_consul_backend(
        host=CONSUL_HOST, port=CONSUL_PORT, prefix=consul_seeded
    )


API_HIER_LAYERS = [
    {"name": "global", "url": f"{HTTP_BASE}/v1/hier/global/parameters"},
    {"name": "production", "url": f"{HTTP_BASE}/v1/hier/production/parameters"},
    {"name": "eu-west", "url": f"{HTTP_BASE}/v1/hier/eu-west/parameters"},
]


@pytest.fixture(scope="session")
def backend_api():
    pytest.importorskip("requests")
    from read_config_core.api import ApiBackend

    return ApiBackend(
        layers=API_HIER_LAYERS,
        auth_token=HTTP_TOKEN,
        allowed_hosts=["127.0.0.1"],
    )


@pytest.fixture(scope="session")
def backend_filesystem(tmp_path_factory):
    """The same hierarchy on disk, for comparison against the remote stores.

    Note the shape difference: a filesystem hierarchy is rooted *at* config_dir,
    which is itself a config level. SQL/KV chains start at the first path
    segment and have no such implicit root. The "global" level therefore lives
    at the root directory here, but at a named "global" key there.
    """
    from read_config_core.filesystem import FilesystemBackend

    root = tmp_path_factory.mktemp("fs-hierarchy")
    layout = {
        "": HIERARCHY["global"],
        "production": HIERARCHY["global/production"],
        "production/eu-west": HIERARCHY["global/production/eu-west"],
    }
    for relative, payload in layout.items():
        directory = root / relative if relative else root
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{ROLE}.json").write_text(json.dumps(payload))

    return FilesystemBackend(root=str(root), format_type="json")


# name -> (fixture name, deepest location, ordered per-level locations)
PARITY_BACKENDS = {
    "postgres": ("backend_postgres", DEEPEST, list(HIERARCHY)),
    "mariadb": ("backend_mariadb", DEEPEST, list(HIERARCHY)),
    "redis": ("backend_redis", DEEPEST, list(HIERARCHY)),
    "etcd": ("backend_etcd", DEEPEST, list(HIERARCHY)),
    "consul": ("backend_consul", DEEPEST, list(HIERARCHY)),
    "api": ("backend_api", "eu-west", ["global", "production", "eu-west"]),
    "filesystem": ("backend_filesystem", None, None),   # resolved below
}


class ParityCase:
    """One backend plus the locations that address its three levels."""

    def __init__(self, name, backend, deepest, levels):
        self.name = name
        self.backend = backend
        self.deepest = deepest
        self.levels = levels

    def __iter__(self):
        # Kept tuple-unpackable for readability at the call sites.
        return iter((self.name, self.backend, self.deepest))


@pytest.fixture(params=sorted(PARITY_BACKENDS), scope="session")
def parity_backend(request):
    """A ParityCase for every backend, live stores and filesystem alike."""
    name = request.param
    fixture_name, deepest, levels = PARITY_BACKENDS[name]
    backend = request.getfixturevalue(fixture_name)

    if name == "filesystem":
        root = backend.root
        levels = [root, f"{root}/production", f"{root}/production/eu-west"]
        deepest = levels[-1]

    return ParityCase(name, backend, deepest, levels)


# --- running the module itself against the live stores ---------------------

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "plugins" / "modules" / "read_config.py"


@pytest.fixture
def invoke_module(tmp_path: Path):
    """Invoke read_config.py as a real Ansible module subprocess.

    Mirrors tests/integration's harness, but the module talks to the live
    containers instead of a temp directory.
    """
    import sys

    def _invoke(args: dict, *, env: dict[str, str] | None = None) -> dict:
        args_file = tmp_path / "args.json"
        args_file.write_text(json.dumps({"ANSIBLE_MODULE_ARGS": args}))

        child_env = {**os.environ}
        # etcd3's 2021-era protobuf stubs need the pure-Python implementation
        # to import under protobuf >= 4.
        child_env.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")
        child_env.update(env or {})

        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), str(args_file)],
            capture_output=True,
            text=True,
            env=child_env,
            timeout=60,
            check=False,
        )
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"module stdout was not valid JSON (exit={proc.returncode})\n"
                f"stdout: {proc.stdout!r}\nstderr: {proc.stderr!r}"
            ) from exc

    return _invoke


def pytest_collection_modifyitems(config, items) -> None:
    """Auto-apply the live marker to everything under tests/live."""
    for item in items:
        if "tests/live" in str(item.fspath).replace(os.sep, "/"):
            item.add_marker(pytest.mark.live)
