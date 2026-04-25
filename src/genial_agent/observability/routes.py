"""Handlers Starlette pour ``/health`` et ``/stats`` — montés par
``mount.py`` sur l'app FastAPI Chainlit.

Sécurité : aucun champ de ``snapshot()`` ne contient de PII ni de
secret (compteurs d'agrégats uniquement). L'URL Pappers complète est
server-only par construction (cf. ``mcp_pappers._build_url``). La
comparaison du ``STATS_TOKEN`` est timing-safe via
``hmac.compare_digest`` (review B3).
"""

from __future__ import annotations

import asyncio
import hmac
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from genial_agent import __version__, mcp_pappers
from genial_agent.observability.stats import snapshot

# Cap dur sur le ping MCP côté ``/health`` (review M3) : Pappers peut
# avoir un cold start ou un read lent — on ne fait pas attendre
# UptimeRobot 15 s (le timeout réseau de ``mcp_pappers``). 3 s aligne
# avec ``app.py:_HEALTHCHECK_TIMEOUT_S`` au boot d'un chat.
_HEALTH_MCP_TIMEOUT_S: float = 3.0


async def health(request: Request) -> JSONResponse:  # noqa: ARG001 — handler signature Starlette
    """Healthcheck riche : ping MCP Pappers + meta version/uptime.

    Code HTTP **toujours 200** (cf. story phase 1 §"/health contrat") ;
    le statut effectif est dans ``body["status"]``. UptimeRobot (S08)
    lira ce champ via son keyword check, pas par le code HTTP — un
    flap MCP transient ne doit pas page Lancelot dimanche soir.

    Timeout interne (review M3) : ``_HEALTH_MCP_TIMEOUT_S`` borne le
    ping Pappers (par défaut 3 s) pour ne pas tenir UptimeRobot ouvert
    sur un MCP coincé. Au-delà, on retourne ``status="ko"`` cohérent
    avec le contrat 4 clés.
    """
    try:
        mcp = await asyncio.wait_for(
            mcp_pappers.healthcheck(),
            timeout=_HEALTH_MCP_TIMEOUT_S,
        )
    except TimeoutError:
        mcp = {
            "status": "ko",
            "latency_ms": int(_HEALTH_MCP_TIMEOUT_S * 1000),
            "tools_count": 0,
            "error": "TimeoutError",
        }
    snap = snapshot()
    return JSONResponse(
        {
            "status": mcp["status"],
            "mcp": mcp,
            "version": __version__,
            "uptime_s": snap["uptime_s"],
        }
    )


_BEARER_PREFIX = "Bearer "

# Marqueurs **runtime-only** posés par la plateforme Railway dans le
# container du service en exécution. Ne PAS lister ici les vars que les
# devs stockent typiquement dans leur ``.env`` local pour requêter la
# GraphQL API Railway (``RAILWAY_PROJECT_ID``, ``RAILWAY_SERVICE_ID``,
# ``RAILWAY_ENVIRONMENT_ID``, ``RAILWAY_API_TOKEN``,
# ``RAILWAY_PUBLIC_DOMAIN``) — sinon faux-positif "on est en prod" sur
# tout poste de dev. On retient les variantes ``_NAME`` (humaines, ne
# servent à rien hors runtime) + ``RAILWAY_DEPLOYMENT_ID`` /
# ``RAILWAY_REPLICA_ID`` (per-instance, jamais en .env). La présence
# d'un seul suffit (la plateforme en injecte toujours plusieurs en
# parallèle).
_RAILWAY_RUNTIME_MARKERS = (
    "RAILWAY_DEPLOYMENT_ID",
    "RAILWAY_REPLICA_ID",
    "RAILWAY_SERVICE_NAME",
    "RAILWAY_PROJECT_NAME",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PRIVATE_DOMAIN",
)


def _is_running_on_railway() -> bool:
    return any(os.getenv(name) for name in _RAILWAY_RUNTIME_MARKERS)


async def stats(request: Request) -> JSONResponse:
    """Compteurs cumulatifs. Auth obligatoire en prod Railway.

    Politique (review S08 §B2 — durcie suite à la review post-deploy) :

    - **Prod Railway** (au moins un ``RAILWAY_*`` env var posé par la
      plateforme) : ``STATS_TOKEN`` obligatoire. Absent → 503 explicite,
      pas de fuite des compteurs sur l'URL publique.
    - **Local dev** (aucun marqueur Railway) : si ``STATS_TOKEN`` est
      défini → Bearer requis ; sinon endpoint ouvert (DX simple,
      ``curl localhost:8000/stats``).

    Comparaison **timing-safe** via ``hmac.compare_digest`` (review B3) :
    bonne pratique OWASP pour tout secret comparé à une entrée user,
    même si en pratique TLS + jitter réseau noient le signal — c'est
    un signal de qualité enterprise (Cegid / CA, cf. cahier §14).
    """
    token = os.getenv("STATS_TOKEN")

    if _is_running_on_railway() and not token:
        # Refus explicite plutôt que silently servir : un /stats ouvert
        # sur l'URL publique fuiterait volumétrie + coût Anthropic +
        # crédits Pappers résiduels (signal de mode dégradé exploitable).
        return JSONResponse(
            {
                "error": "stats_token_required_in_production",
                "hint": (
                    "Set STATS_TOKEN env var on the Railway service "
                    '(generate via: python -c "import secrets; '
                    'print(secrets.token_urlsafe(32))").'
                ),
            },
            status_code=503,
        )

    if token:
        auth = request.headers.get("authorization", "")
        if not auth.startswith(_BEARER_PREFIX):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        provided = auth[len(_BEARER_PREFIX) :]
        if not hmac.compare_digest(provided, token):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(snapshot())
