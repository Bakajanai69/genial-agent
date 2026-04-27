"""Prepend de ``/v1/chat/completions`` (+ ``/voice-meta.html``) sur Chainlit.

Calqué sur le pattern ``observability/mount.py`` (S07) :

- Idempotent via ``_MOUNTED`` (rebound côté tests via ``reset_for_tests``).
- Import tardif de ``chainlit.server.app`` à l'intérieur de la fonction
  pour ne pas charger Chainlit au module-load (utile aux tests S07-like
  qui n'ont pas besoin de Chainlit).
- **No-op si ``settings.ENABLE_VOICE_MODE`` est faux** — la route n'est
  même pas enregistrée. Surface d'attaque nulle même si
  ``ELEVEN_AGENT_SHARED_TOKEN`` fuite (défense en profondeur).
- Override propre via ``_purge_existing_routes`` : ré-mount n'accumule
  pas les routes (pattern S07 phase 3 review M4).

``/voice-meta.html`` est un endpoint de bootstrap qui sert un meta tag
HTML (``<meta name="genial-voice-mode" content="true" data-agent-id="…">``)
côté navigateur. Le bootstrap JS (``public/eleven-widget-bootstrap.js``)
le fetch au load et injecte le widget convai si voice mode est actif.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import HTMLResponse
from starlette.routing import Route

from genial_agent.config import settings
from genial_agent.voice.openai_adapter import chat_completions

_MOUNTED = False
_OWNED_PATHS = ("/v1/chat/completions", "/voice-meta.html")


async def _voice_meta(_request: Request) -> HTMLResponse:
    """Sert un mini HTML avec un meta tag voice-mode + data-agent-id.

    Format consommé par ``public/eleven-widget-bootstrap.js`` qui
    injecte ``<elevenlabs-convai>`` côté navigateur seulement si le
    flag est ``true`` et que l'agent-id est fourni.
    """
    enabled = "true" if settings.ENABLE_VOICE_MODE else "false"
    agent_id = settings.ELEVEN_AGENT_ID
    # Echappement basique des attributs HTML (ces valeurs viennent de
    # variables d'env contrôlées côté serveur — pas d'input user — mais
    # on reste prudent).
    safe_agent_id = agent_id.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")
    html = (
        "<!doctype html>\n"
        "<html><head>\n"
        f'<meta name="genial-voice-mode" content="{enabled}" '
        f'data-agent-id="{safe_agent_id}">\n'
        "</head><body></body></html>\n"
    )
    return HTMLResponse(html, status_code=200)


def _purge_existing_routes(routes: list) -> None:
    """Retire les ``Route`` Starlette dont le ``path`` est dans
    ``_OWNED_PATHS``. Mute la liste in-place (ne pas réassigner —
    cf. ``observability/mount.py`` pour le rationale).
    """
    indices_to_remove = [
        i
        for i, r in enumerate(routes)
        if isinstance(r, Route) and getattr(r, "path", None) in _OWNED_PATHS
    ]
    for i in reversed(indices_to_remove):
        del routes[i]


def mount_voice_routes() -> None:
    """Prepend ``/v1/chat/completions`` + ``/voice-meta.html`` sur l'app
    Chainlit. **No-op si ``settings.ENABLE_VOICE_MODE`` est faux**.
    """
    global _MOUNTED
    if _MOUNTED:
        return
    if not settings.ENABLE_VOICE_MODE:
        return

    # Import tardif (évite de charger chainlit dans les tests qui n'en
    # ont pas besoin).
    from chainlit.server import app as cl_app

    routes = cl_app.router.routes
    _purge_existing_routes(routes)
    routes.insert(
        0,
        Route(
            "/v1/chat/completions",
            chat_completions,
            methods=["POST"],
        ),
    )
    routes.insert(
        1,
        Route(
            "/voice-meta.html",
            _voice_meta,
            methods=["GET", "HEAD"],
        ),
    )
    _MOUNTED = True


def reset_for_tests() -> None:
    """Force un re-mount au prochain ``mount_voice_routes()`` — tests only."""
    global _MOUNTED
    _MOUNTED = False
