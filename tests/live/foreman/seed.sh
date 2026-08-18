#!/usr/bin/env bash
#
# Seed the live Foreman with the entity hierarchy the adapter tests expect.
# Idempotent: every step tolerates the object already existing, so it is safe
# to re-run against a warm container.
set -uo pipefail

FOREMAN_URL="${FOREMAN_URL:-http://127.0.0.1:13000}"
AUTH="${FOREMAN_AUTH:-admin:changeme}"

api() { curl -s -u "$AUTH" -H 'Content-Type: application/json' "$@"; }
post() { api -X POST -d "$2" "${FOREMAN_URL}/api/v2/$1" >/dev/null 2>&1; }

# Entities. Organization 1 / Location 2 come from Foreman's own seed.
post domains    '{"domain":{"name":"eu.example.com","organization_ids":[1],"location_ids":[2]}}'
post hostgroups '{"hostgroup":{"name":"web","organization_ids":[1],"location_ids":[2]}}'
post hosts      '{"host":{"name":"web01.eu.example.com","managed":false,
                          "organization_id":1,"location_id":2,
                          "domain_id":1,"hostgroup_id":1}}'

# Parameters, one level at a time, so precedence is observable:
#   workers: organization 2 -> hostgroup 4 -> host 8
param() { post "$1/parameters" "{\"parameter\":{\"name\":\"$2\",\"value\":\"$3\"}}"; }
param organizations/1 workers       2
param organizations/1 region        org-wide
param locations/2     log_level     warn
param domains/1       search_domain eu.example.com
param hostgroups/1    workers       4
param hostgroups/1    role_tier     frontend
param hosts/1         workers       8
param hosts/1         hostname      web01

echo "foreman seeded"
