"""Middleware HTTP qui garantit la présence du cookie ``genial_owner_id``
sur **toute requête entrante** servie par Chainlit, avant que
``header_auth_callback`` (``app.py``) ne lise les cookies.

Pourquoi ce middleware
----------------------

Le cookie ``genial_owner_id`` est la source d'identité durable du
visiteur (persistant 1 an, isolé par navigateur). Sans lui, Chainlit ne
peut pas afficher la sidebar des conversations passées et les threads
créés sont liés à un identifier ``anon-<uuid>`` éphémère qui
disparaît au prochain pageload — orphelins définitifs.

Auparavant, le cookie n'était posé que côté client par
``public/eleven-widget-bootstrap.js``. Race condition : sur le 1er
pageload, le navigateur n'a aucun cookie à envoyer, ``header_auth_callback``
retombait sur le fallback ``anon-<uuid>``, et tout thread créé entre ce
moment et la 1re re-requête HTTP était perdu.

Le middleware ferme cette fenêtre :

1. **Sur la requête entrante** : si ``genial_owner_id`` est absent ou
   malformé, on génère un UUID hex 32 chars et on l'**injecte dans
   ``request.scope["headers"]``** pour que ``header_auth_callback``
   (qui s'exécute après) le voie comme un cookie réel.
2. **Sur la réponse sortante** : on ajoute un ``Set-Cookie:
   genial_owner_id=<uuid>; Max-Age=31536000; Path=/; SameSite=Lax;
   Secure`` (le ``Secure`` est conditionné à HTTPS pour rester
   compatible avec un dev local en HTTP).

Le JS côté client continue à synchroniser ``localStorage`` ↔ cookie en
redondance — utile pour reposer le cookie si l'utilisateur vide
manuellement les cookies du navigateur sans toucher au localStorage.

Aucune authentification réelle ici : pas de password, pas d'OAuth, pas
de session signée. Le cookie est un simple identifiant de scope (comme
un session-storage par navigateur) qui sert uniquement à isoler les
threads dans la sidebar Chainlit.
"""

from __future__ import annotations

import re
import secrets
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

logger = structlog.get_logger(__name__)


OWNER_COOKIE_NAME = "genial_owner_id"
"""Nom du cookie partagé avec ``public/eleven-widget-bootstrap.js`` et
``ui/chainlit_data_layer.py:OWNER_COOKIE_NAME``."""


OWNER_COOKIE_RE = re.compile(rf"\b{OWNER_COOKIE_NAME}=([A-Za-z0-9-]{{8,64}})")
"""Pattern conservateur pour matcher une **valeur valide** du cookie.
On exclut les caractères qui pourraient indiquer une injection (HTML,
quotes…) et on borne la longueur."""


_OWNER_COOKIE_ANY_RE = re.compile(rf"(?:^|;\s*){OWNER_COOKIE_NAME}=[^;]*")
"""Pattern **permissif** qui matche le segment ``genial_owner_id=…``
quel que soit son format (vide, malformé, court, etc.). Utilisé
uniquement pour le **scrub** d'un header avant ré-injection — pas pour
décider d'une identité."""


_COOKIE_MAX_AGE_S = 31_536_000
"""1 an. Durée alignée avec ``public/eleven-widget-bootstrap.js`` —
le cookie sert à retrouver les threads d'un visiteur entre sessions."""


def generate_owner_id() -> str:
    """Génère un identifiant ``genial_owner_id`` neuf — 32 chars hex.

    On utilise ``secrets`` (pas ``uuid.uuid4()``) pour souligner que
    la valeur est sensible vis-à-vis de l'isolation des threads (un
    attaquant qui devine un ``owner_id`` lit les threads du visiteur).
    32 chars hex = 128 bits d'entropie, infraisable à deviner.
    """
    return secrets.token_hex(16)


def _extract_existing_owner_id(cookie_header: str) -> str | None:
    """Extrait l'``owner_id`` depuis un header ``Cookie:`` brut.

    Retourne ``None`` si absent ou malformé (ex : valeur vide,
    caractères suspects). Le cookie sera regénéré dans ce cas.
    """
    if not cookie_header:
        return None
    match = OWNER_COOKIE_RE.search(cookie_header)
    if match is None:
        return None
    return match.group(1)


def _inject_cookie_into_request_headers(
    scope_headers: list[tuple[bytes, bytes]],
    owner_id: str,
) -> list[tuple[bytes, bytes]]:
    """Reconstruit la liste de headers ``scope["headers"]`` en
    s'assurant que le header ``Cookie`` contient bien
    ``genial_owner_id=<owner_id>``.

    Comportement :

    - S'il n'y a pas de header ``Cookie`` du tout, on en ajoute un.
    - S'il existe et contient déjà ``genial_owner_id`` (cas où le
      cookie est arrivé du client mais malformé — on a regénéré),
      on **remplace** la valeur dans le header au lieu d'append.
    - Sinon, on append ``; genial_owner_id=<owner_id>`` à la valeur
      existante du header ``Cookie``.

    On garde la même liste en sortie (ne pas muter en place pour rester
    compatible avec ``request.scope`` qui peut être référencé ailleurs).
    """
    new_pair = (
        b"cookie",
        f"{OWNER_COOKIE_NAME}={owner_id}".encode("ascii"),
    )

    cookie_indices = [i for i, (k, _) in enumerate(scope_headers) if k == b"cookie"]
    if not cookie_indices:
        return [*scope_headers, new_pair]

    rebuilt: list[tuple[bytes, bytes]] = []
    appended = False
    for i, (key, value) in enumerate(scope_headers):
        if i not in cookie_indices:
            rebuilt.append((key, value))
            continue
        try:
            decoded = value.decode("ascii", errors="replace")
        except UnicodeDecodeError:
            decoded = ""
        # Scrub TOUTE occurrence de ``genial_owner_id=…`` (valide ou
        # malformée) puis nettoie les ``; `` orphelins. On ré-append
        # ensuite la nouvelle valeur propre. Évite les doublons et la
        # cohabitation d'un cookie BAD avec le bon dans le même header.
        cleaned = _OWNER_COOKIE_ANY_RE.sub("", decoded)
        cleaned = re.sub(r"^\s*;\s*", "", cleaned)
        cleaned = re.sub(r";\s*;", ";", cleaned)
        cleaned = cleaned.strip("; \t")
        new_value = (
            f"{cleaned}; {OWNER_COOKIE_NAME}={owner_id}"
            if cleaned
            else f"{OWNER_COOKIE_NAME}={owner_id}"
        )
        rebuilt.append((b"cookie", new_value.encode("ascii")))
        appended = True
    # Cas dégénéré (filtré tous les headers cookie) : on append.
    if not appended:
        rebuilt.append(new_pair)
    return rebuilt


def _is_html_pageload(request: Request) -> bool:
    """Heuristique : vrai si la requête est un pageload HTML
    (``GET`` + ``Accept: text/html``).

    Pourquoi cette restriction
    --------------------------

    Au tout 1er pageload (cookie/localStorage absents), le navigateur
    envoie en parallèle 30-50 requêtes (HTML, CSS, JS, fonts, images,
    favicon, manifest, etc.). En HTTP/2 multiplex, ces requêtes
    partent **avant** que la 1re réponse ``Set-Cookie`` n'arrive — le
    navigateur n'a donc encore aucun cookie à envoyer.

    Si on mint un ``owner_id`` distinct sur **chaque** requête, on
    pose 50 ``Set-Cookie`` différents. Le navigateur garde "le
    dernier" reçu, dans un ordre dépendant de la latence — et la
    WebSocket Chainlit peut s'ouvrir avec un cookie encore différent
    (= race condition, le bug initial qu'on essayait de fermer).

    En limitant le mint au pageload HTML uniquement, on garantit
    qu'**un seul** ``Set-Cookie`` est posé par cycle pageload — la
    valeur stockée par le navigateur est déterministe, et toutes les
    requêtes suivantes (assets, WebSocket) envoient ce même cookie.
    """
    if request.method != "GET":
        return False
    accept = request.headers.get("accept", "").lower()
    return "text/html" in accept


def _request_is_secure(request: Request) -> bool:
    """Détecte HTTPS y compris derrière un proxy TLS (Railway, Heroku,
    Cloudflare).

    Railway termine TLS à son edge load balancer et passe en HTTP au
    backend → ``request.url.scheme == "http"`` malgré le HTTPS côté
    client. On lit donc en priorité ``X-Forwarded-Proto`` que les
    proxies posent par convention.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto", "").lower()
    if forwarded_proto:
        return forwarded_proto == "https"
    return request.url.scheme == "https"


async def ensure_owner_cookie_dispatch(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Dispatch du middleware FastAPI/Starlette.

    Compatible avec ``BaseHTTPMiddleware`` (signature
    ``(request, call_next) -> Response``) et avec le decorator
    ``@app.middleware("http")`` qui passe la même signature.

    On ne mint que sur les pageloads HTML (``_is_html_pageload``) pour
    éviter le multi-mint en cas de pageload parallèle (cf. docstring
    de ``_is_html_pageload``).
    """
    cookie_header = request.headers.get("cookie", "")
    existing = _extract_existing_owner_id(cookie_header)

    minted_id: str | None = None
    if existing is None and _is_html_pageload(request):
        minted_id = generate_owner_id()
        # Inject le cookie dans le scope ASGI pour que
        # ``header_auth_callback`` (Chainlit) le voie comme un cookie
        # reçu du client. Sur un upgrade WebSocket subséquent, le
        # navigateur enverra le cookie posé en réponse — pas besoin
        # d'inject côté WS handshake (BaseHTTPMiddleware ne l'attrape
        # de toute façon pas).
        request.scope["headers"] = _inject_cookie_into_request_headers(
            request.scope.get("headers", []),
            minted_id,
        )
        logger.info(
            "auth_owner_cookie_minted",
            identifier_prefix=minted_id[:8],
            scheme=request.url.scheme,
            path=request.url.path,
        )

    response = await call_next(request)

    if minted_id is not None:
        is_secure = _request_is_secure(request)
        response.set_cookie(
            key=OWNER_COOKIE_NAME,
            value=minted_id,
            max_age=_COOKIE_MAX_AGE_S,
            path="/",
            samesite="lax",
            secure=is_secure,
            # Pas de ``HttpOnly`` : le JS doit pouvoir relire et
            # synchroniser ce cookie avec ``localStorage`` (cf.
            # ``public/eleven-widget-bootstrap.js``).
            httponly=False,
        )
    return response
