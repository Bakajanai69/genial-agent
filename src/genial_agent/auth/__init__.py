"""Module ``auth`` : pose silencieuse du cookie d'isolation des threads
côté serveur dès la 1re requête HTTP.

Le cookie ``genial_owner_id`` était auparavant posé **uniquement** côté
client par ``public/eleven-widget-bootstrap.js``. Conséquence : sur le
1er pageload (cookie absent du HTTP entrant), ``header_auth_callback``
retombait sur un identifier ``anon-XXXX`` éphémère et les threads
créés à ce moment devenaient orphelins (cf. logs
``chainlit_data_layer_thread_access_denied``).

Ce module ferme cette fenêtre en injectant le cookie côté serveur
**avant** que ``header_auth_callback`` lise les headers — l'utilisateur
voit immédiatement un User stable et persistant.
"""

from __future__ import annotations

from genial_agent.auth.middleware import (
    OWNER_COOKIE_NAME,
    OWNER_COOKIE_RE,
    ensure_owner_cookie_dispatch,
    generate_owner_id,
)
from genial_agent.auth.mount import mount_auth_middleware

__all__ = [
    "OWNER_COOKIE_NAME",
    "OWNER_COOKIE_RE",
    "ensure_owner_cookie_dispatch",
    "generate_owner_id",
    "mount_auth_middleware",
]
