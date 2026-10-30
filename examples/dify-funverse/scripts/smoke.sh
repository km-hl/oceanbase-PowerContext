#!/usr/bin/env bash
set -euo pipefail

# Run after docker compose up -d --build.
if [[ -z "${FUN_API_TOKEN:-}" ]]; then
  echo "Export FUN_API_TOKEN first (never put it in screenshots)." >&2
  exit 2
fi
base="${FUN_API_BASE_URL:-http://127.0.0.1:8765}"
player="smoke-$(date +%s)-$$"
headers=(-H "X-Fun-Token: $FUN_API_TOKEN" -H "Content-Type: application/json")

echo "[1/4] Check real seekdb health"
curl -fsS "$base/healthz"
echo
echo "[2/4] Write one chapter"
curl -fsS "${headers[@]}" -X POST "$base/v1/save" \
  -d "{\"player_id\":\"$player\",\"turn_id\":\"smoke-1\",\"choice\":\"开一家火星火锅店\",\"chapter\":\"外星人嫌我的红油太红\"}"
echo
echo "[3/4] Read it back"
curl -fsS "${headers[@]}" -X POST "$base/v1/context" \
  -d "{\"player_id\":\"$player\",\"choice\":\"火星火锅店\"}"
echo
echo "[4/4] Confirm persisted row count using seekdb's SQL protocol"
docker exec funverse-seekdb sh -c 'mysql -h127.0.0.1 -P2881 -uroot -p"$ROOT_PASSWORD" -D funverse -N -e "SELECT COUNT(*) FROM funverse_chapters;"'
echo "Smoke run completed; verify that the response includes the newly saved chapter."
