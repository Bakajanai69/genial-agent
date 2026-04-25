#!/usr/bin/env bash
# Smoke test post-deploy idempotent — aligné docs/deployment.md §7.
# Sortie 0 si tout est vert, ≥ 1 sinon. À jouer AVANT d'envoyer le
# lien à Fabien.
set -euo pipefail

DOMAIN="${SMOKE_DOMAIN:-genial-agent-production.up.railway.app}"
URL="https://${DOMAIN}"

# ``--max-time 10`` : on borne chaque requête. /health Railway répond
# < 500 ms en EU-West (cf. story phase 1 §D), donc 10 s est très
# large mais protège contre un cold start ou une latence Cloudflare
# anormale (review S09 §S-1 — ne pas hang indéfiniment).
CURL=(curl -fsS --max-time 10)

echo "→ /health"
HEALTH=$("${CURL[@]}" "${URL}/health")
echo "${HEALTH}" | jq .
STATUS=$(echo "${HEALTH}" | jq -r '.status')
TOOLS=$(echo "${HEALTH}" | jq -r '.mcp.tools_count')
[[ "${STATUS}" == "ok" ]] || { echo "✗ status != ok (got '${STATUS}')"; exit 1; }
# Null-safe : ``jq -r`` retourne ``"null"`` (string) si la clé manque,
# ce qui ferait crasher l'arithmétique bash avec un message cryptique.
# On vérifie explicitement que c'est un entier ≥ 1 (review S09 §S-3).
if ! [[ "${TOOLS}" =~ ^[0-9]+$ ]] || (( TOOLS < 1 )); then
  echo "✗ tools_count='${TOOLS}' (attendu : entier ≥ 1)"
  exit 2
fi

echo "→ / (UI)"
# ``-iE`` : HTTP/2 Railway envoie les en-têtes en minuscules
# (``content-type``), HTTP/1.1 les capitalise (``Content-Type``).
# Case-insensitive pour matcher les deux (review S09 §S-2).
"${CURL[@]}" -I "${URL}/" | grep -iE "HTTP|content-type"

echo "→ /stats (si STATS_TOKEN)"
if [[ -n "${STATS_TOKEN:-}" ]]; then
  "${CURL[@]}" "${URL}/stats" -H "Authorization: Bearer ${STATS_TOKEN}" | jq .
else
  echo "  (skip — STATS_TOKEN non défini en local)"
fi

echo "✓ smoke OK"
