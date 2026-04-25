"""Prepend des routes ``/health`` et ``/stats`` sur l'app FastAPI Chainlit.

Le catch-all ``@router.get("/{full_path:path}")`` de Chainlit est
ajouté en dernier au moment du ``import chainlit`` (cf. phase 1
inspection — Chainlit 2.11.1). En prepend-ant nos routes en index 0/1,
Starlette les matche avant le catch-all et avant le ``/health``
statique natif Chainlit.

Idempotent via ``_MOUNTED`` — appeler plusieurs fois est sans effet
(utile dans les tests qui partagent ``chainlit.server.app``).

**Override propre du /health natif** (review M4) : on **retire**
explicitement les routes existantes ``/health`` et ``/stats`` (que
ce soit le ``/health`` statique de Chainlit ou un mount précédent
d'un test) avant de prepend nos versions. Cela évite l'accumulation
de routes au fil des resets et garantit qu'aucun handler natif ne
court-circuite le nôtre, même après un upgrade Chainlit.
"""

from __future__ import annotations

from starlette.routing import Route

from genial_agent.observability.routes import health, stats

_MOUNTED = False
_OWNED_PATHS = ("/health", "/stats")


def _purge_existing_routes(routes: list) -> None:
    """Retire les ``Route`` Starlette dont le ``path`` est dans
    ``_OWNED_PATHS``. Mute la liste en place (``cl_app.router.routes``
    est cette même liste — ré-assigner ferait perdre le binding du
    router FastAPI).
    """
    indices_to_remove = [
        i
        for i, r in enumerate(routes)
        if isinstance(r, Route) and getattr(r, "path", None) in _OWNED_PATHS
    ]
    # Suppression en ordre décroissant pour ne pas invalider les indices.
    for i in reversed(indices_to_remove):
        del routes[i]


def mount_routes() -> None:
    """Prepend ``/health`` (override Chainlit statique) et ``/stats``.

    Méthodes acceptées : ``GET`` + ``HEAD`` (review N3). UptimeRobot
    et certains health-probes utilisent ``HEAD`` (économie bande
    passante) ; sans ``HEAD``, Starlette renvoie 405.
    """
    global _MOUNTED
    if _MOUNTED:
        return
    # Import tardif : retarde le coût d'import chainlit jusqu'au boot
    # de l'app. Permet aussi aux tests S07 unit qui ne touchent pas
    # aux routes (idempotence, stats, credit_guard, logging) de ne
    # pas charger chainlit.
    from chainlit.server import app as cl_app

    routes = cl_app.router.routes
    _purge_existing_routes(routes)
    routes.insert(0, Route("/health", health, methods=["GET", "HEAD"]))
    routes.insert(1, Route("/stats", stats, methods=["GET", "HEAD"]))
    _MOUNTED = True


def reset_for_tests() -> None:
    """Force un re-mount au prochain ``mount_routes`` — tests only.

    Le prochain ``mount_routes()`` purgera lui-même les routes
    précédentes (cf. ``_purge_existing_routes``), donc on ne risque
    pas l'accumulation cross-test.
    """
    global _MOUNTED
    _MOUNTED = False
