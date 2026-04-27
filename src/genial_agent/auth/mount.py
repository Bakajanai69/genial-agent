"""Hook le middleware ``ensure_owner_cookie`` sur l'app FastAPI Chainlit.

Pattern jumeau d'``observability/mount.py`` (idempotent via ``_MOUNTED``,
import tardif de ``chainlit.server.app``). Appelé une seule fois au boot
depuis ``app.py`` — re-imports tests-only sont sans effet.

Ordre des middlewares Starlette : Starlette les applique en LIFO
(``add_middleware`` ajouté en dernier = appelé en premier). On l'ajoute
au boot avant que Chainlit serve la 1re requête, pour qu'il s'exécute
**avant** ``header_auth_callback``.
"""

from __future__ import annotations

import structlog

from genial_agent.auth.middleware import ensure_owner_cookie_dispatch

logger = structlog.get_logger(__name__)

_MOUNTED = False


def mount_auth_middleware() -> None:
    """Hook ``ensure_owner_cookie`` sur ``chainlit.server.app``.

    Idempotent : un second appel est sans effet (utile en tests qui
    réimportent ``app.py``).
    """
    global _MOUNTED
    if _MOUNTED:
        return
    # Import tardif : ne charge ``chainlit`` que si on est en runtime
    # serveur, pas dans les tests unit qui touchent uniquement la
    # logique pure du middleware.
    from chainlit.server import app as cl_app
    from starlette.middleware.base import BaseHTTPMiddleware

    cl_app.add_middleware(
        BaseHTTPMiddleware,
        dispatch=ensure_owner_cookie_dispatch,
    )
    _MOUNTED = True
    logger.info("auth_middleware_mounted")


def reset_for_tests() -> None:
    """Force un re-mount au prochain ``mount_auth_middleware`` — tests
    only. Pas appelé en runtime."""
    global _MOUNTED
    _MOUNTED = False
