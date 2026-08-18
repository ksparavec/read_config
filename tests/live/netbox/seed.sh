#!/usr/bin/env bash
#
# Seed NetBox with a device and two config contexts, so the adapter has a
# server-rendered config_context to read. Idempotent: NetBox rejects duplicate
# names, and those failures are ignored.
set -uo pipefail

NETBOX_URL="${NETBOX_URL:-http://127.0.0.1:18000}"
AUTH="${NETBOX_AUTH:?NETBOX_AUTH must be the v2 token, nbt_<key>.<token>}"

post() {
  curl -s -o /dev/null -H "Authorization: Bearer ${AUTH}" \
       -H 'Content-Type: application/json' \
       -X POST -d "$2" "${NETBOX_URL}/api/$1"
}

post dcim/manufacturers/ '{"name":"RcVendor","slug":"rcvendor"}'
post dcim/device-types/   '{"model":"RcModel","slug":"rcmodel","manufacturer":1,"u_height":1}'
post dcim/sites/          '{"name":"RcSite","slug":"rcsite"}'
post dcim/device-roles/   '{"name":"RcRole","slug":"rcrole","color":"aabbcc"}'
post dcim/devices/        '{"name":"web01","device_type":1,"role":1,"site":1,"status":"active"}'

# Two contexts NetBox merges server-side by weight: the role context (200)
# overrides the site context (100), and nested dicts are deep-merged.
post extras/config-contexts/ '{"name":"rc-base","weight":100,"sites":[1],
  "data":{"listen_port":8080,"workers":2,"log_level":"info",
          "database":{"pool_size":10,"host":"db.default.internal"}}}'
post extras/config-contexts/ '{"name":"rc-role","weight":200,"roles":[1],
  "data":{"workers":8,"log_level":"warn","database":{"pool_size":50}}}'

echo "netbox seeded"
