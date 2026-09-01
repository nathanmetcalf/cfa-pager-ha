#!/usr/bin/env bash
# Deploy the integration to Home Assistant and validate the config before restarting.
#
# HA config is CIFS-mounted at /mnt/tmp. A custom integration's Python is only loaded at
# startup, so code changes need a core restart, not a reload.
#
# Usage: deploy.sh [--restart]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
HA_CONFIG="${HA_CONFIG:-/mnt/tmp}"
HA_URL="${HA_URL:-http://172.20.30.10:8123}"
TOKEN_FILE="${TOKEN_FILE:-$HOME/.config/trailcam/ha_token}"

[ -d "$HA_CONFIG/.storage" ] || { echo "HA config not mounted at $HA_CONFIG" >&2; exit 1; }
TOKEN="$(cat "$TOKEN_FILE")"

api() {  # api METHOD PATH [BODY]
  local method="$1" path="$2" body="${3:-}"
  if [ -n "$body" ]; then
    curl -sS -X "$method" -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" -d "$body" "$HA_URL$path"
  else
    curl -sS -X "$method" -H "Authorization: Bearer $TOKEN" "$HA_URL$path"
  fi
}

echo "== copying the integration"
# --inplace because CIFS races on rsync's temp files: it creates the directory then
# writes .name.XXXXXX inside it before the client cache sees the directory exist.
# --no-perms/owner/group because the mount forces uid, gid and modes anyway.
mkdir -p "$HA_CONFIG/custom_components/cfa_pager"
sync
rsync -rlt --inplace --delete --no-perms --no-owner --no-group --exclude '__pycache__' \
  "$REPO/custom_components/cfa_pager/" "$HA_CONFIG/custom_components/cfa_pager/"
ls -1 "$HA_CONFIG/custom_components/cfa_pager/" | sed 's/^/   /'

echo "== validating the Home Assistant configuration"
RESULT="$(api POST /api/config/core/check_config)"
echo "   $RESULT"
case "$RESULT" in
  *'"result": "valid"'*|*'"result":"valid"'*) ;;
  *) echo "config invalid, not restarting" >&2; exit 1 ;;
esac

if [ "${1:-}" = "--restart" ]; then
  echo "== restarting Home Assistant"
  api POST /api/services/homeassistant/restart '{}' > /dev/null || true
  echo "   restart requested, waiting for the API to come back"
  for _ in $(seq 1 60); do
    sleep 5
    if api GET /api/ 2>/dev/null | grep -q "API running"; then
      echo "   back up"
      exit 0
    fi
  done
  echo "   did not come back within 5 minutes" >&2
  exit 1
fi
echo "== not restarting (pass --restart). A custom integration only loads at startup."
