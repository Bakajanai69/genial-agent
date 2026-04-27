"""``ContextVar`` qui expose l'``owner_id`` de la requête HTTP courante
au data layer Chainlit, même quand le data layer est invoqué hors
contexte WebSocket (ex : route REST ``/api/thread/<id>`` que la
sidebar Chainlit fetch).

Pourquoi
--------

``_resolve_owner_id`` (cf. ``ui/chainlit_data_layer.py``) avait 3
sources possibles, toutes liées au contexte WebSocket Chainlit :

1. ``cl.user_session.get("user")`` — l'User posé par
   ``header_auth_callback``. Disponible UNIQUEMENT dans une session
   WebSocket active.
2. ``cl.context.session.environ["HTTP_COOKIE"]`` — l'environ ASGI de
   la WebSocket initiale. Idem, contexte WebSocket only.
3. ``cl.user_session.get(SESSION_OWNER_KEY)`` — UUID éphémère par
   session WebSocket, posé par ``on_chat_start``. Différent à chaque
   reconnexion.

Quand Chainlit appelle ``get_thread`` via une route REST HTTP (typique
de la sidebar threads qui fait un fetch HTTP pour charger les détails),
**aucune des 3 priorités ne fonctionne** → fallback sentinel
``__no_owner_resolved__`` → comparaison d'owner avec le thread.user_id
toujours fausse → ``access_denied``.

Le ``ContextVar`` ci-dessous est posé par
``auth/middleware.py:ensure_owner_cookie_dispatch`` pour CHAQUE requête
HTTP (à partir du cookie reçu, ou de l'UUID minté quand le cookie est
absent). Il sert de pont entre le contexte HTTP middleware et le data
layer Chainlit.

Sécurité : le ContextVar est scopé à la task asyncio courante. Pas de
fuite cross-request. Reset systématique en ``finally`` côté middleware.
"""

from __future__ import annotations

from contextvars import ContextVar

current_owner_id: ContextVar[str | None] = ContextVar(
    "genial_current_owner_id",
    default=None,
)
"""Owner_id du visiteur actuellement servi par la requête HTTP en cours.

- ``None`` quand pas de cookie reçu et pas de mint (ex : requête asset
  qui ne déclenche pas de mint).
- Une str hex 8-64 chars sinon.

Lu par ``ui/chainlit_data_layer.py:_resolve_owner_id`` en priorité
intermédiaire (entre les sources WebSocket et le SESSION_OWNER_KEY
éphémère)."""
