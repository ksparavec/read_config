"""The Ansible module itself, run as a subprocess against the live backends.

Everything else in this directory drives the backend classes directly. This
module goes through the real entry point — argument spec, ``backend_options``
plumbing, ``get_backend()`` dispatch, fact assembly, JSON serialization — which
is the path an actual playbook takes.
"""
from __future__ import annotations

import pytest

from .conftest import (
    FOREMAN_AUTH,
    FOREMAN_BASE,
    FOREMAN_HOST,
    CONSUL_HOST,
    CONSUL_PORT,
    DEEPEST,
    ETCD_HOST,
    ETCD_PORT,
    EXPECTED_MERGE,
    HTTP_BASE,
    HTTP_TOKEN,
    MYSQL_DSN,
    PG_DSN,
    REDIS_URL,
    ROLE,
)

pytestmark = pytest.mark.live


def facts(payload: dict) -> dict:
    assert payload.get("failed") is not True, payload.get("msg")
    return payload["ansible_facts"]["read_config"]


# name -> module args (minus config_path), and the location to request.
def _module_args(name: str, fs_root: str) -> tuple[dict, str]:
    if name == "filesystem":
        # The one backend needing no container, driven through the module on
        # exactly the same terms as the remote stores. Its hierarchy is rooted
        # AT config_dir, so config_path is an absolute directory path.
        return {"config_dir": fs_root, "format": "json"}, (
            f"{fs_root}/production/eu-west"
        )
    if name == "postgres":
        return {"backend": "sql", "backend_options": {"table": "role_configs"},
                "backend_secrets": {"dsn": PG_DSN}}, DEEPEST
    if name == "mariadb":
        return {"backend": "sql", "backend_options": {"table": "role_configs"},
                "backend_secrets": {"dsn": MYSQL_DSN}}, DEEPEST
    if name == "redis":
        return {"backend": "redis", "backend_options": {"prefix": "rclive"},
                "backend_secrets": {"url": REDIS_URL}}, DEEPEST
    if name == "etcd":
        return {"backend": "etcd",
                "backend_options": {"host": ETCD_HOST, "port": ETCD_PORT,
                                    "prefix": "rclive"}}, DEEPEST
    if name == "consul":
        return {"backend": "consul",
                "backend_options": {"host": CONSUL_HOST, "port": CONSUL_PORT,
                                    "prefix": "rclive"}}, DEEPEST
    if name == "api":
        # Layer names are deliberately chosen not to occur as substrings of any
        # merged config value: backend_options is no_log, and Ansible scrubs
        # every string in it from the output. See the xfail tests at the bottom
        # of this file for that defect.
        return {"backend": "api",
                "backend_options": {
                    "allowed_hosts": ["127.0.0.1"],
                    "layers": [
                        {"name": "layer_org",
                         "url": f"{HTTP_BASE}/v1/hier/global/parameters"},
                        {"name": "layer_env",
                         "url": f"{HTTP_BASE}/v1/hier/production/parameters"},
                        {"name": "layer_dcx",
                         "url": f"{HTTP_BASE}/v1/hier/eu-west/parameters"},
                    ],
                },
                "backend_secrets": {"auth_token": HTTP_TOKEN}}, "layer_dcx"
    raise AssertionError(name)


BACKENDS = (
    "filesystem", "postgres", "mariadb", "redis", "etcd", "consul", "api",
)


@pytest.fixture
def module_args(backend_filesystem):
    """Build module args for a backend by name.

    A fixture rather than a plain function because the filesystem case needs
    the seeded tmp root, which only a fixture can supply.
    """
    def _build(name: str) -> tuple[dict, str]:
        return _module_args(name, backend_filesystem.root)

    return _build


@pytest.fixture(params=BACKENDS)
def live_backend(request, backend_filesystem, backend_postgres, backend_mariadb,
                 backend_redis, backend_etcd, backend_consul, backend_api):
    """Name of a backend, with its store already seeded."""
    return request.param


def test_single_mode_returns_the_merged_config(invoke_module, live_backend, module_args) -> None:
    args, location = module_args(live_backend)

    result = invoke_module({"role_name": ROLE, "config_path": location, **args})

    data = facts(result)
    assert data["mode"] == "single"
    assert data["matched_count"] == 1
    assert data["configs"][location]["data"] == EXPECTED_MERGE, live_backend


def test_provenance_is_reported_for_every_source(invoke_module, live_backend, module_args) -> None:
    args, location = module_args(live_backend)

    result = invoke_module({"role_name": ROLE, "config_path": location, **args})

    merged = facts(result)["configs"][location]["meta"]["files_merged"]
    assert len(merged) == 3, live_backend
    if live_backend != "api":
        # HTTP identifiers are the rendered URLs, which live in backend_options
        # and are therefore scrubbed to a single sentinel by no_log.
        # See test_provenance_is_not_redacted_by_no_log.
        assert len(set(merged)) == 3, live_backend


def test_multi_mode_discovers_locations(invoke_module, live_backend, module_args) -> None:
    args, location = module_args(live_backend)

    result = invoke_module({"role_name": ROLE, **args})

    data = facts(result)
    assert data["mode"] == "multiple"
    if live_backend == "api":
        # ApiBackend.discover reports only the deepest applicable layer.
        assert data["matched_count"] == 1
        assert data["configs"]["layer_dcx"]["data"] == EXPECTED_MERGE
    else:
        assert data["matched_count"] >= 3, live_backend
        assert data["configs"][location]["data"] == EXPECTED_MERGE


def test_dry_run_reports_sources_without_data(invoke_module, live_backend, module_args) -> None:
    args, location = module_args(live_backend)

    result = invoke_module(
        {"role_name": ROLE, "config_path": location, "dry_run": True, **args}
    )

    entry = facts(result)["configs"][location]
    assert entry["data"] == {}, live_backend
    assert len(entry["meta"]["files_merged"]) == 3, live_backend


def test_schema_validation_accepts_the_merged_config(
    invoke_module, live_backend, module_args, tmp_path
) -> None:
    import json

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "type": "object",
        "required": ["workers", "database"],
        "properties": {
            "workers": {"type": "integer"},
            "database": {"type": "object"},
        },
    }))
    args, location = module_args(live_backend)

    result = invoke_module({
        "role_name": ROLE, "config_path": location,
        "validate_schema": str(schema), **args,
    })

    assert facts(result)["configs"][location]["data"] == EXPECTED_MERGE


def test_schema_violation_fails_the_task(
    invoke_module, live_backend, module_args, tmp_path
) -> None:
    import json

    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "type": "object",
        "required": ["workers"],
        "properties": {"workers": {"type": "string"}},   # it is an integer
    }))
    args, location = module_args(live_backend)

    result = invoke_module({
        "role_name": ROLE, "config_path": location,
        "validate_schema": str(schema), **args,
    })

    assert result.get("failed") is True, live_backend
    assert "Schema validation failed" in result["msg"]


def test_config_tag_filters_out_non_matching_configs(
    invoke_module, live_backend, module_args
) -> None:
    """None of the seeded configs carry a config_tag, so all are filtered."""
    args, location = module_args(live_backend)

    result = invoke_module({
        "role_name": ROLE, "config_path": location,
        "config_tag": "production", **args,
    })

    data = facts(result)
    assert data["matched_count"] == 0, live_backend
    assert data["configs"] == {}, live_backend


def test_track_changes_is_rejected_for_non_filesystem_backends(
    invoke_module, live_backend, module_args
) -> None:
    if live_backend == "filesystem":
        pytest.skip("filesystem is the backend that supports it; see the next test")
    args, location = module_args(live_backend)

    result = invoke_module({
        "role_name": ROLE, "config_path": location, "track_changes": True, **args,
    })

    assert result.get("failed") is True, live_backend
    assert "track_changes is only supported for the filesystem backend" in result["msg"]


def test_track_changes_works_on_the_filesystem_backend(
    invoke_module, module_args, tmp_path
) -> None:
    """The positive half of the rule above, on the one backend that allows it.

    Uses a private copy of the hierarchy so the checksum side effect cannot
    leak into other tests through the session-scoped fixture root.
    """
    import json as _json
    import shutil

    args, location = module_args("filesystem")
    root = tmp_path / "fs"
    shutil.copytree(args["config_dir"], root)
    args = {"config_dir": str(root), "format": "json"}
    target = str(root / "production" / "eu-west")

    first = invoke_module({
        "role_name": ROLE, "config_path": target, "track_changes": True, **args,
    })

    # No previous checksum file exists, so the first run always reports changed.
    assert first["changed"] is True
    assert len(facts(first)["changed_files"]) == 3
    assert facts(first)["configs"][target]["data"] == EXPECTED_MERGE

    checksums = root / f".{ROLE}_checksums.json"
    assert checksums.is_file(), "checksum file must land inside config_dir"

    # Nothing changed on disk -> no drift reported.
    second = invoke_module({
        "role_name": ROLE, "config_path": target, "track_changes": True, **args,
    })
    assert second["changed"] is False
    assert "changed_files" not in facts(second)

    # Edit a source -> drift is detected and named.
    leaf = root / "production" / "eu-west" / f"{ROLE}.json"
    payload = _json.loads(leaf.read_text())
    payload["region"] = "eu-central"
    leaf.write_text(_json.dumps(payload))

    third = invoke_module({
        "role_name": ROLE, "config_path": target, "track_changes": True, **args,
    })
    assert third["changed"] is True
    assert str(leaf) in facts(third)["changed_files"]


def test_unknown_location_is_reported_clearly(invoke_module, module_args) -> None:
    """HTTP names its valid layers; a typo should say so."""
    args, _location = module_args("api")

    result = invoke_module({"role_name": ROLE, "config_path": "typo", **args})

    assert result.get("failed") is True
    assert "Unknown layer 'typo'" in result["msg"]


def test_backend_options_are_redacted_from_failure_output(invoke_module) -> None:
    """no_log=True must keep the DSN password out of the task output.

    The redaction is thorough enough to mask the offending value itself, which
    is worth knowing when debugging a malformed backend_options.
    """
    result = invoke_module({
        "role_name": ROLE,
        "backend": "sql",
        "backend_options": {"table": "bad;table"},
        "backend_secrets": {"dsn": PG_DSN},
    })

    assert result.get("failed") is True
    blob = repr(result)
    assert "rcpass" not in blob, "DSN password leaked into module output"
    # The structural value is no longer masked, so the error is diagnosable.
    assert "bad;table" in blob, "structural options should stay readable"


def test_bad_credentials_fail_without_leaking_the_password(
    invoke_module, expect_container_error
) -> None:
    # Postgres logs a FATAL for the rejected login; that is this test working.
    expect_container_error(r"password authentication failed for user")

    result = invoke_module({
        "role_name": ROLE,
        "config_path": DEEPEST,
        "backend": "sql",
        "backend_options": {"table": "role_configs"},
        "backend_secrets": {
            "dsn": "postgresql+psycopg://rcuser:WRONGPASS@127.0.0.1:15432/rcdb",
        },
    })

    assert result.get("failed") is True
    assert "WRONGPASS" not in repr(result)


# --- no_log must not reach structural values --------------------------------
#
# Ansible implements no_log by substring-scrubbing every string AND number in
# the marked value out of the module's output. When backend_options itself was
# marked no_log, a KV prefix, a SQL table name, an API layer name, or a numeric
# context id would be scrubbed from the merged config: a context id of 5 turned
# every value containing a "5" into a sentinel, and flipped its type.
#
# Splitting credentials into backend_secrets confines the scrubbing to actual
# secrets. These tests hold that line.

def test_provenance_is_not_redacted(invoke_module) -> None:
    """Structural options live outside no_log, so provenance stays readable."""
    result = invoke_module({
        "role_name": ROLE,
        "config_path": DEEPEST,
        "backend": "redis",
        "backend_options": {"prefix": "rclive"},
        "backend_secrets": {"url": REDIS_URL},
    })

    merged = facts(result)["configs"][DEEPEST]["meta"]["files_merged"]

    assert all("***" not in source for source in merged), merged


def test_config_data_is_not_redacted(invoke_module) -> None:
    """A layer named 'eu-west' must not redact the config value 'eu-west'."""
    result = invoke_module({
        "role_name": ROLE,
        "config_path": "eu-west",
        "backend": "api",
        "backend_secrets": {"auth_token": HTTP_TOKEN},
        "backend_options": {
            "allowed_hosts": ["127.0.0.1"],
            "layers": [
                {"name": "global", "url": f"{HTTP_BASE}/v1/hier/global/parameters"},
                {"name": "production",
                 "url": f"{HTTP_BASE}/v1/hier/production/parameters"},
                {"name": "eu-west", "url": f"{HTTP_BASE}/v1/hier/eu-west/parameters"},
            ],
        },
    })

    data = facts(result)["configs"]["eu-west"]["data"]

    assert data["region"] == "eu-west"
    assert data["database"]["host"] == "db.eu-west.internal"


# --- api presets through the module ----------------------------------------

def test_foreman_preset_via_module(invoke_module) -> None:
    """The whole point of an adapter: name the API and the host, get the rest."""
    result = invoke_module({
        "role_name": ROLE,
        "config_path": "host",
        "backend": "api",
        "backend_options": {
            "api": "foreman",
            "base_url": FOREMAN_BASE,
            "host": FOREMAN_HOST,
            "allowed_hosts": ["127.0.0.1"],
        },
        "backend_secrets": {"auth": FOREMAN_AUTH},
    })

    data = facts(result)["configs"]["host"]["data"]

    assert data["workers"] == "8"
    assert data["log_level"] == "warn"
    assert data["role_tier"] == "frontend"
    # The seeded host has no subnet, so Foreman reports subnet_id: null and
    # that level simply is not in the chain.
    assert len(facts(result)["configs"]["host"]["meta"]["files_merged"]) == 6


def test_foreman_preset_excludes_a_level(invoke_module) -> None:
    result = invoke_module({
        "role_name": ROLE,
        "config_path": "host",
        "backend": "api",
        "backend_options": {
            "api": "foreman",
            "base_url": FOREMAN_BASE,
            "host": FOREMAN_HOST,
            "excludes": ["domain", "hostgroup"],
            "allowed_hosts": ["127.0.0.1"],
        },
        "backend_secrets": {"auth": FOREMAN_AUTH},
    })

    entry = facts(result)["configs"]["host"]
    assert len(entry["meta"]["files_merged"]) == 4
    assert "role_tier" not in entry["data"]


def test_unknown_preset_fails_with_the_known_names(invoke_module) -> None:
    result = invoke_module({
        "role_name": ROLE,
        "backend": "api",
        "backend_options": {"api": "nosuchvendor", "base_url": FOREMAN_BASE,
                            "host": FOREMAN_HOST},
    })

    assert result.get("failed") is True
    assert "Unknown api preset" in result["msg"]
    assert "foreman" in result["msg"]


def test_preset_without_base_url_fails_clearly(invoke_module) -> None:
    result = invoke_module({
        "role_name": ROLE,
        "backend": "api",
        "backend_options": {"api": "foreman", "host": FOREMAN_HOST},
    })

    assert result.get("failed") is True
    assert "requires base_url" in result["msg"]


def test_numeric_context_ids_do_not_corrupt_numeric_config(invoke_module) -> None:
    """The regression that motivated splitting backend_secrets out.

    A numeric context id used to be collected as a no_log value and then
    substring-matched against everything in the output: with organization_id
    5, the values 5, 50 and 8500 all became
    'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER' and changed type from int to str.
    """
    result = invoke_module({
        "role_name": "probe",
        "config_path": "probe_layer",
        "backend": "api",
        "backend_options": {
            "allowed_hosts": ["127.0.0.1"],
            "organization_id": 5,
            "layers": [{
                "name": "probe_layer",
                "url": f"{HTTP_BASE}/v1/probe/vals/{{organization_id}}",
            }],
        },
        "backend_secrets": {"auth_token": HTTP_TOKEN},
    })

    data = facts(result)["configs"]["probe_layer"]["data"]

    assert data == {
        "exact": 5,
        "contains": 50,
        "embedded": 8500,
        "clean": 8080,
        "in_string": "costs 5 eur",
    }
    assert all(isinstance(data[k], int)
               for k in ("exact", "contains", "embedded", "clean"))


def test_a_key_may_not_appear_in_both_option_dicts(invoke_module) -> None:
    result = invoke_module({
        "role_name": ROLE,
        "backend": "sql",
        "backend_options": {"dsn": "sqlite:///x.db"},
        "backend_secrets": {"dsn": PG_DSN},
    })

    assert result.get("failed") is True
    assert "may not appear in both" in result["msg"]
    assert "dsn" in result["msg"]
