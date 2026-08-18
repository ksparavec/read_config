#!/usr/bin/env bash
#
# Seed AWX with an inventory -> group -> host chain carrying variables at each
# level, so the adapter can merge them in Ansible's own precedence.
# Idempotent: AWX rejects duplicate names, and those failures are ignored.
set -uo pipefail

AWX_URL="${AWX_URL:-http://127.0.0.1:18052}"
AUTH="${AWX_AUTH:-admin:changeme}"

post() {
  curl -s -o /dev/null -u "$AUTH" -H 'Content-Type: application/json' \
       -X POST -d "$2" "${AWX_URL}/api/v2/$1"
}

post organizations/ '{"name":"RcOrg"}'
post inventories/   '{"name":"RcInv","organization":1,
                      "variables":"{\"listen_port\": 8080, \"workers\": 2, \"log_level\": \"info\"}"}'
post groups/        '{"name":"web","inventory":1,
                      "variables":"{\"workers\": 4, \"role_tier\": \"frontend\"}"}'
post hosts/         '{"name":"web01","inventory":1,
                      "variables":"{\"workers\": 8, \"hostname\": \"web01\"}"}'
post groups/1/hosts/ '{"id":1}'

echo "awx seeded"
