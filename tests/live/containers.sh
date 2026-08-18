#!/usr/bin/env bash
#
# Podman lifecycle for the read_config live backend suite.
#
# Every backend service runs in its own container on a dedicated, non-default
# host port so the suite never collides with a locally installed Postgres,
# Redis, etc. Containers are started detached and in parallel, then waited on
# concurrently, so the whole fleet is ready in roughly the time the slowest
# single service takes.
#
# The containers are servers only. Test code runs on the host and talks to
# them over localhost via the normal client libraries.
#
# Usage:
#   ./containers.sh up        start every service and block until all are ready
#   ./containers.sh down      remove every service (database survives)
#   ./containers.sh reset     remove every service AND wipe the database
#   ./containers.sh status    one line per service: name, port, state, ready
#   ./containers.sh logs      dump all logs (optionally: logs <service>)
#   ./containers.sh errors    print log lines matching the error patterns
#   ./containers.sh ready     re-run the readiness probes only
#
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${RC_LIVE_PREFIX:-rclive}"
PODMAN="${PODMAN:-podman}"
READY_TIMEOUT="${RC_LIVE_READY_TIMEOUT:-600}"

# PostgreSQL data lives on the host so the products' first-boot migrations
# (~1900 of them across Foreman, NetBox and AWX) survive a down/up cycle.
# Wipe it with `containers.sh reset` to force a clean rebuild.
PGDATA_DIR="${RC_LIVE_PGDATA:-${HERE}/.pgdata}"

# Foreman alone holds ~11 connections at idle, and three products plus the
# suite's own backends share this server.
PG_MAX_CONNECTIONS="${RC_LIVE_PG_MAX_CONNECTIONS:-300}"

# Test-only credentials. These are fixtures, not secrets: the services bind to
# localhost and hold nothing but generated test data.
PG_USER=rcuser
PG_PASS=rcpass
PG_DB=rcdb
MY_ROOT_PASS=rcroot
MY_USER=rcuser
MY_PASS=rcpass
MY_DB=rcdb
REDIS_PASS=rcpass
HTTP_TOKEN=rc-test-token
FOREMAN_USER=admin
FOREMAN_PASS=changeme
FOREMAN_DB_PASS=foreman
NETBOX_USER=admin
NETBOX_PASS=changeme
NETBOX_KEY=rcliveKey123
NETBOX_TOKEN=0123456789abcdef0123456789abcdef01234567
NETBOX_AUTH="nbt_${NETBOX_KEY}.${NETBOX_TOKEN}"
NETBOX_SECRET='rc-live-suite-secret-key-that-is-at-least-fifty-characters-long'
NETBOX_PEPPER='rc-live-suite-api-token-pepper-one-at-least-fifty-characters-long'
AWX_USER=admin
AWX_PASS=changeme

# service|image|host_port|container_port
#
# Pinned to explicit versions so a re-pull can never silently change what the
# suite tests against. Bump deliberately, then re-run `make live`.
SERVICES=(
  "postgres|docker.io/library/postgres:17-alpine|15432|5432"
  "mariadb|docker.io/library/mariadb:12.3|13306|3306"
  "redis|docker.io/library/redis:8.10-alpine|16379|6379"
  "etcd|quay.io/coreos/etcd:v3.7.1|12379|2379"
  "consul|docker.io/hashicorp/consul:1.22.7|18500|8500"
  "nginx|docker.io/library/nginx:1.29-alpine|18080|80"
  # A real Foreman, not a fixture: the api preset's foreman adapter is written
  # against this product's API, so it is tested against the product. Needs its
  # own PostgreSQL, and pays a db:migrate + db:seed on first boot.
  "foreman|quay.io/foreman/foreman:foreman-3.16.0|13000|3000"
  "netbox|docker.io/netboxcommunity/netbox:v4.6.8|18000|8080"
  "awx|quay.io/ansible/awx:24.6.1|18052|8052"
)

# Services that must reach each other by name share a private network.
NETWORK="${PREFIX}-net"

field() { echo "$1" | cut -d'|' -f"$2"; }
cname() { echo "${PREFIX}-$1"; }

svc_names() {
  local spec
  for spec in "${SERVICES[@]}"; do field "$spec" 1; done
}

svc_port() {
  local spec
  for spec in "${SERVICES[@]}"; do
    [ "$(field "$spec" 1)" = "$1" ] && { field "$spec" 3; return; }
  done
}

svc_image() {
  local spec
  for spec in "${SERVICES[@]}"; do
    [ "$(field "$spec" 1)" = "$1" ] && { field "$spec" 2; return; }
  done
}

# --- start -----------------------------------------------------------------

start_one() {
  local svc="$1" name port image
  name="$(cname "$svc")"
  port="$(svc_port "$svc")"
  image="$(svc_image "$svc")"

  # Idempotent: leave an already-running container alone.
  if [ "$($PODMAN inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = "true" ]; then
    echo "exists  $svc"
    return 0
  fi
  $PODMAN rm -f "$name" >/dev/null 2>&1

  case "$svc" in
    postgres)
      # Also hosts the foreman / netbox / awx databases, created by the init
      # script, so those products need no server of their own. The data
      # directory is bind-mounted from the host: the init script only runs
      # when it is empty, so a warm start skips every product's migrations.
      mkdir -p "$PGDATA_DIR"
      # Rootless podman maps the container's postgres user (uid 70) into the
      # caller's subuid range; chown inside that namespace so it can write.
      $PODMAN unshare chown -R 70:70 "$PGDATA_DIR" 2>/dev/null
      $PODMAN run -d --name "$name" --network "$NETWORK" --network-alias pg \
        -p "127.0.0.1:${port}:5432" \
        -e POSTGRES_USER="$PG_USER" -e POSTGRES_PASSWORD="$PG_PASS" \
        -e POSTGRES_DB="$PG_DB" \
        -v "${PGDATA_DIR}:/var/lib/postgresql/data:Z" \
        -v "${HERE}/initdb:/docker-entrypoint-initdb.d:ro,Z" \
        "$image" -c "max_connections=${PG_MAX_CONNECTIONS}" >/dev/null
      ;;
    mariadb)
      $PODMAN run -d --name "$name" -p "127.0.0.1:${port}:3306" \
        -e MARIADB_ROOT_PASSWORD="$MY_ROOT_PASS" \
        -e MARIADB_USER="$MY_USER" -e MARIADB_PASSWORD="$MY_PASS" \
        -e MARIADB_DATABASE="$MY_DB" \
        "$image" >/dev/null
      ;;
    redis)
      # Doubles as netbox's and awx's cache/broker on separate db indices.
      $PODMAN run -d --name "$name" --network "$NETWORK" --network-alias cache \
        -p "127.0.0.1:${port}:6379" \
        "$image" \
        redis-server --requirepass "$REDIS_PASS" >/dev/null
      ;;
    etcd)
      # Runs unauthenticated to keep the fixture simple; the backend's auth
      # surface is exercised by the redis (password) and http (token) services.
      $PODMAN run -d --name "$name" -p "127.0.0.1:${port}:2379" \
        "$image" \
        /usr/local/bin/etcd \
        --name rc-etcd \
        --data-dir /etcd-data \
        --listen-client-urls http://0.0.0.0:2379 \
        --advertise-client-urls http://0.0.0.0:2379 \
        --log-level warn >/dev/null
      ;;
    consul)
      $PODMAN run -d --name "$name" -p "127.0.0.1:${port}:8500" \
        "$image" \
        agent -dev -client=0.0.0.0 -log-level=warn >/dev/null
      ;;
    nginx)
      # Serves the static JSON fixture tree that the api backend's layered
      # REST model reads. nginx emits real ETag headers for static files, so
      # this also exercises ApiBackend's ETag fingerprint path.
      $PODMAN run -d --name "$name" -p "127.0.0.1:${port}:80" \
        -v "${HERE}/http/html:/usr/share/nginx/html:ro,Z" \
        -v "${HERE}/http/nginx.conf:/etc/nginx/conf.d/default.conf:ro,Z" \
        "$image" >/dev/null
      ;;
    foreman)
      # Waits for its database itself: the image runs db:migrate and db:seed
      # before booting Rails, and both retry until Postgres answers.
      $PODMAN run -d --name "$name" --network "$NETWORK" \
        -p "127.0.0.1:${port}:3000" \
        -v "${HERE}/foreman/database.yml:/etc/foreman/database.yml:ro,Z" \
        -e SEED_ADMIN_USER="$FOREMAN_USER" \
        -e SEED_ADMIN_PASSWORD="$FOREMAN_PASS" \
        -e SEED_ADMIN_EMAIL=admin@example.com \
        "$image" >/dev/null
      ;;
    netbox)
      # SECRET_KEY and the API token pepper must each be >= 50 characters,
      # and a v2 API token needs both a 12-character key and the token.
      $PODMAN run -d --name "$name" --network "$NETWORK" \
        -p "127.0.0.1:${port}:8080" \
        -e DB_HOST=pg -e DB_NAME=netbox -e DB_USER=netbox -e DB_PASSWORD=netbox \
        -e REDIS_HOST=cache -e REDIS_PASSWORD="$REDIS_PASS" -e REDIS_DATABASE=3 \
        -e REDIS_CACHE_HOST=cache -e REDIS_CACHE_PASSWORD="$REDIS_PASS" \
        -e REDIS_CACHE_DATABASE=4 \
        -e SECRET_KEY="$NETBOX_SECRET" \
        -e API_TOKEN_PEPPER_1="$NETBOX_PEPPER" \
        -e ALLOWED_HOSTS='*' \
        -e SUPERUSER_NAME="$NETBOX_USER" -e SUPERUSER_PASSWORD="$NETBOX_PASS" \
        -e SUPERUSER_EMAIL=admin@example.com \
        -e SUPERUSER_API_TOKEN="$NETBOX_TOKEN" \
        -e SUPERUSER_API_KEY="$NETBOX_KEY" \
        "$image" >/dev/null
      ;;
    awx)
      # AWX ships a web stack whose nginx wants port 80, which rootless
      # podman refuses. Only the read API is exercised here, so Django serves
      # it directly after migrating and creating the admin user.
      $PODMAN run -d --name "$name" --network "$NETWORK" \
        -p "127.0.0.1:${port}:8052" \
        -v "${HERE}/awx/settings.py:/etc/tower/settings.py:ro,Z" \
        -e DJANGO_SUPERUSER_PASSWORD="$AWX_PASS" \
        "$image" /bin/bash -c "
          until (echo > /dev/tcp/pg/5432) 2>/dev/null; do sleep 2; done &&
          awx-manage migrate --no-input &&
          (awx-manage createsuperuser --noinput --username $AWX_USER \
             --email admin@example.com || true) &&
          exec awx-manage runserver 0.0.0.0:8052 --noreload --insecure" >/dev/null
      ;;
    *)
      echo "unknown service: $svc" >&2
      return 1
      ;;
  esac
  echo "started $svc"
}

# --- readiness -------------------------------------------------------------

probe() {
  local svc="$1" name port
  name="$(cname "$svc")"
  port="$(svc_port "$svc")"
  case "$svc" in
    # Both server images run a socket-only bootstrap instance during first
    # init and then restart the real one. Probing over TCP (which the
    # bootstrap instance does not listen on) is what makes readiness mean
    # "actually serving", rather than "briefly answering on a unix socket".
    postgres) $PODMAN exec "$name" pg_isready -q -h 127.0.0.1 -p 5432 \
                -U "$PG_USER" -d "$PG_DB" ;;
    mariadb)  $PODMAN exec "$name" mariadb-admin ping --silent \
                --protocol=TCP -h 127.0.0.1 -P 3306 \
                -u"$MY_USER" -p"$MY_PASS" ;;
    redis)    [ "$($PODMAN exec "$name" redis-cli -a "$REDIS_PASS" --no-auth-warning ping 2>/dev/null)" = "PONG" ] ;;
    etcd)     $PODMAN exec -e ETCDCTL_API=3 "$name" etcdctl \
                --endpoints=http://127.0.0.1:2379 endpoint health ;;
    consul)   curl -fsS "http://127.0.0.1:${port}/v1/status/leader" 2>/dev/null | grep -q ':' ;;
    # Fetches a real fixture rather than /healthz: checking out a branch
    # replaces the mounted directory's inodes, leaving the container serving a
    # stale mount. That shows up as a working server with 404s everywhere, so
    # readiness has to mean "the fixtures are actually visible".
    nginx)    curl -fsS -o /dev/null -H "Authorization: Bearer ${HTTP_TOKEN}" \
                "http://127.0.0.1:${port}/v1/hier/global/parameters" 2>/dev/null ;;
    foreman)  curl -fsS -u "${FOREMAN_USER}:${FOREMAN_PASS}" \
                "http://127.0.0.1:${port}/api/v2/status" 2>/dev/null \
                | grep -q '"result":"ok"' ;;
    netbox)   curl -fsS -H "Authorization: Bearer ${NETBOX_AUTH}" \
                "http://127.0.0.1:${port}/api/status/" 2>/dev/null \
                | grep -q 'netbox-version' ;;
    awx)      curl -fsS -u "${AWX_USER}:${AWX_PASS}" \
                "http://127.0.0.1:${port}/api/v2/ping/" 2>/dev/null \
                | grep -q '"version"' ;;
  esac
}

wait_one() {
  local svc="$1" deadline
  deadline=$(( $(date +%s) + READY_TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if probe "$svc" >/dev/null 2>&1; then
      echo "ready   $svc"
      return 0
    fi
    # Surface a container that died rather than burning the full timeout.
    if [ "$($PODMAN inspect -f '{{.State.Running}}' "$(cname "$svc")" 2>/dev/null)" != "true" ]; then
      echo "DEAD    $svc" >&2
      $PODMAN logs --tail 30 "$(cname "$svc")" >&2 2>&1
      return 1
    fi
    sleep 1
  done
  echo "TIMEOUT $svc after ${READY_TIMEOUT}s" >&2
  $PODMAN logs --tail 30 "$(cname "$svc")" >&2 2>&1
  return 1
}

cmd_up() {
  local svc pids=() rc=0
  $PODMAN network exists "$NETWORK" 2>/dev/null || \
    $PODMAN network create "$NETWORK" >/dev/null
  for svc in $(svc_names); do start_one "$svc" & pids+=($!); done
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  [ "$rc" -ne 0 ] && { echo "one or more services failed to start" >&2; return 1; }

  pids=()
  for svc in $(svc_names); do wait_one "$svc" & pids+=($!); done
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  [ "$rc" -ne 0 ] && { echo "one or more services never became ready" >&2; return 1; }

  # Used by `containers.sh errors` to scope its scan past the startup phase.
  # The pytest guard does not read this; it anchors to its own session start
  # so one run never inherits the previous run's errors.
  # Foreman needs its entity hierarchy before the adapter tests can read it.
  FOREMAN_URL="http://127.0.0.1:$(svc_port foreman)" \
    FOREMAN_AUTH="${FOREMAN_USER}:${FOREMAN_PASS}" \
    "${HERE}/foreman/seed.sh" >/dev/null
  NETBOX_URL="http://127.0.0.1:$(svc_port netbox)" \
    NETBOX_AUTH="$NETBOX_AUTH" "${HERE}/netbox/seed.sh" >/dev/null
  AWX_URL="http://127.0.0.1:$(svc_port awx)" \
    AWX_AUTH="${AWX_USER}:${AWX_PASS}" "${HERE}/awx/seed.sh" >/dev/null

  date -u +%Y-%m-%dT%H:%M:%SZ > "${HERE}/.ready_at"
  echo "all services ready"
}

cmd_ready() {
  local svc pids=() rc=0
  for svc in $(svc_names); do wait_one "$svc" & pids+=($!); done
  for pid in "${pids[@]}"; do wait "$pid" || rc=1; done
  return $rc
}

cmd_down() {
  local svc pids=()
  for svc in $(svc_names); do
    ( $PODMAN rm -f "$(cname "$svc")" >/dev/null 2>&1; echo "removed $svc" ) &
    pids+=($!)
  done
  for pid in "${pids[@]}"; do wait "$pid"; done
  $PODMAN network rm -f "$NETWORK" >/dev/null 2>&1
  rm -f "${HERE}/.ready_at"
}

cmd_reset() {
  cmd_down
  echo "removing $PGDATA_DIR"
  $PODMAN unshare rm -rf "$PGDATA_DIR" 2>/dev/null || rm -rf "$PGDATA_DIR"
  echo "database wiped; the next 'up' rebuilds every product from scratch"
}

cmd_status() {
  local svc name state
  printf '%-10s %-7s %-10s %s\n' SERVICE PORT STATE READY
  for svc in $(svc_names); do
    name="$(cname "$svc")"
    state="$($PODMAN inspect -f '{{.State.Status}}' "$name" 2>/dev/null || echo absent)"
    if probe "$svc" >/dev/null 2>&1; then ready=yes; else ready=no; fi
    printf '%-10s %-7s %-10s %s\n' "$svc" "$(svc_port "$svc")" "$state" "$ready"
  done
}

cmd_logs() {
  local svc
  for svc in "${@:-$(svc_names)}"; do
    echo "=================== $svc ==================="
    $PODMAN logs "$(cname "$svc")" 2>&1
  done
}

# Mirrors the pattern set in conftest.py::LogScanner so the CLI and the test
# assertions agree on what counts as an error.
ERROR_RE='(FATAL|PANIC|CRITICAL|\[ERROR\]|ERROR:|Segmentation fault|OOM|out of memory|corrupt)'

cmd_errors() {
  local svc since found=0 out
  since="$(cat "${HERE}/.ready_at" 2>/dev/null || true)"
  for svc in $(svc_names); do
    if [ -n "$since" ]; then
      out="$($PODMAN logs --since "$since" "$(cname "$svc")" 2>&1)"
    else
      out="$($PODMAN logs "$(cname "$svc")" 2>&1)"
    fi
    out="$(echo "$out" | grep -aE "$ERROR_RE" || true)"
    if [ -n "$out" ]; then
      found=1
      echo "--- $svc ---"
      echo "$out"
    fi
  done
  if [ "$found" -eq 0 ]; then
    echo "no error lines in any container log"
  else
    echo
    echo "note: some tests provoke server errors on purpose (bad credentials,"
    echo "      malformed identifiers). The pytest guard knows which of these"
    echo "      are expected; this raw view does not."
  fi
  return 0
}

case "${1:-}" in
  up)     cmd_up ;;
  down)   cmd_down ;;
  reset)  cmd_reset ;;
  ready)  cmd_ready ;;
  status) cmd_status ;;
  logs)   shift; cmd_logs "$@" ;;
  errors) cmd_errors ;;
  *)      sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
