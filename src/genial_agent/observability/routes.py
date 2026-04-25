"""Handlers Starlette pour ``/health`` et ``/stats`` — montés par
``mount.py`` sur l'app FastAPI Chainlit.

Sécurité : aucun champ de ``snapshot()`` ne contient de PII ni de
secret (compteurs d'agrégats uniquement). L'URL Pappers complète est
server-only par construction (cf. ``mcp_pappers._build_url``).
"""

from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import JSONResponse

from genial_agent import __version__, mcp_pappers
from genial_agent.observability.stats import snapshot


async def health(request: Request) -> JSONResponse:  # noqa: ARG001 — handler signature Starlette
    """Healthcheck riche : ping MCP Pappers + meta version/uptime.

    Code HTTP **toujours 200** (cf. story phase 1 §"/health contrat") ;
    le statut effectif est dans ``body["status"]``. UptimeRobot (S08)
    lira ce champ via son keyword check, pas par le code HTTP — un
    flap MCP transient ne doit pas page Lancelot dimanche soir.
    """
    mcp = await mcp_pappers.healthcheck()
    snap = snapshot()
    return JSONResponse(
        {
            "status": mcp["status"],
            "mcp": mcp,
            "version": __version__,
            "uptime_s": snap["uptime_s"],
        }
    )


async def stats(request: Request) -> JSONResponse:
    """Compteurs cumulatifs. Optionnellement protégé par ``STATS_TOKEN``.

    - ``STATS_TOKEN`` non défini → endpoint ouvert (choix MVP démo, la
      surface d'attaque est minime — compteurs anonymisés).
    - ``STATS_TOKEN`` défini → ``Authorization: Bearer <token>`` requis.
    """
    token = os.getenv("STATS_TOKEN")
    if token:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[len("Bearer ") :] != token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(snapshot())
