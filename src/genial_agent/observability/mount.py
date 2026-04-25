"""Prepend des routes ``/health`` et ``/stats`` sur l'app FastAPI Chainlit.

Le catch-all ``@router.get("/{full_path:path}")`` de Chainlit est
ajouté en dernier au moment du ``import chainlit`` (cf. phase 1
inspection — Chainlit 2.11.1). En prepend-ant nos routes en index 0/1,
Starlette les matche avant le catch-all et avant le ``/health``
statique natif Chainlit.

Idempotent via ``_MOUNTED`` — appeler plusieurs fois est sans effet
(utile dans les tests qui partagent ``chainlit.server.app``).
"""

from __future__ import annotations

from starlette.routing import Route

from genial_agent.observability.routes import health, stats

_MOUNTED = False


def mount_routes() -> None:
    """Prepend ``/health`` (override Chainlit statique) et ``/stats``.

    Note : l'override volontaire du ``/health`` natif Chainlit nous
    permet de retourner un payload riche (ping MCP, version, uptime)
    plutôt que ``{"status": "ok"}`` statique. Un upgrade futur de
    Chainlit (3.x) qui retire ce ``/health`` natif n'aura aucun
    impact sur notre handler — il continuera à matcher en premier.
    """
    global _MOUNTED
    if _MOUNTED:
        return
    # Import tardif : retarde le coût d'import chainlit jusqu'au boot
    # de l'app. Permet aussi aux tests S07 unit qui ne touchent pas
    # aux routes (idempotence, stats, credit_guard, logging) de ne
    # pas charger chainlit.
    from chainlit.server import app as cl_app

    cl_app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    cl_app.router.routes.insert(1, Route("/stats", stats, methods=["GET"]))
    _MOUNTED = True


def reset_for_tests() -> None:
    """Force un re-mount au prochain ``mount_routes`` — tests only.

    Ne désinstalle pas les routes déjà prepend-ées (Starlette ne le
    permet pas proprement) : c'est OK car les tests successifs voient
    juste deux paires de routes ``/health`` + ``/stats`` toutes deux
    prioritaires sur le catch-all, dont la 1ère matche.
    """
    global _MOUNTED
    _MOUNTED = False
