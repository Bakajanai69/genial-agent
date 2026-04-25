#!/usr/bin/env bash
# Smoke test post-deploy idempotent — aligné docs/deployment.md §7.
# Sortie 0 si tout est vert, ≥ 1 sinon. À jouer AVANT d'envoyer le
# lien à Fabien.
set -euo pipefail

DOMAIN="${SMOKE_DOMAIN:-genial-agent-production.up.railway.app}"
URL="https://${DOMAIN}"

echo "→ /health"
HEALTH=$(curl -fsS "${URL}/health")
echo "${HEALTH}" | jq .
STATUS=$(echo "${HEALTH}" | jq -r '.status')
TOOLS=$(echo "${HEALTH}" | jq -r '.mcp.tools_count')
[[ "${STATUS}" == "ok" ]] || { echo "✗ status != ok"; exit 1; }
[[ "${TOOLS}" -ge 1 ]] || { echo "✗ tools_count = ${TOOLS}"; exit 2; }

echo "→ / (UI)"
curl -fsI "${URL}/" | grep -E "HTTP|content-type"

echo "→ /stats (si STATS_TOKEN)"
if [[ -n "${STATS_TOKEN:-}" ]]; then
  curl -fsS "${URL}/stats" -H "Authorization: Bearer ${STATS_TOKEN}" | jq .
else
  echo "  (skip — STATS_TOKEN non défini en local)"
fi

echo "✓ smoke OK"
