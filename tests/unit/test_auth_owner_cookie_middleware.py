"""Tests unit pour ``genial_agent.auth.middleware``.

Vérifie 4 invariants :

1. Génération d'un ``owner_id`` propre (32 chars hex, entropie 128 bits).
2. Extraction depuis un header ``Cookie:`` brut — accepte un cookie
   valide, refuse un cookie malformé.
3. Injection dans ``scope["headers"]`` — append si pas de header
   ``Cookie``, remplace la valeur si déjà présente, ne casse pas les
   autres cookies cohabitants.
4. Dispatch end-to-end via ``BaseHTTPMiddleware`` mounté sur une app
   Starlette minimale — vérifie le ``Set-Cookie`` en réponse et que la
   route protégée voit bien le cookie injecté en amont.

Pas de dépendance Chainlit ici — on teste uniquement la logique pure
du middleware (scope ASGI / Starlette). Le ``mount.py`` est testé
implicitement par le boot de ``app.py`` au démarrage Chainlit.
"""

from __future__ import annotations

import re

import pytest
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from genial_agent.auth.middleware import (
    OWNER_COOKIE_NAME,
    _extract_existing_owner_id,
    _inject_cookie_into_request_headers,
    ensure_owner_cookie_dispatch,
    generate_owner_id,
)

# ─── 1. generate_owner_id ─────────────────────────────────────────────


def test_generate_owner_id_format() -> None:
    """32 chars hex, entropie suffisante."""
    owner_id = generate_owner_id()
    assert isinstance(owner_id, str)
    assert len(owner_id) == 32
    assert re.fullmatch(r"[0-9a-f]{32}", owner_id)


def test_generate_owner_id_uniqueness() -> None:
    """Deux appels successifs ne retournent jamais la même valeur."""
    seen = {generate_owner_id() for _ in range(100)}
    assert len(seen) == 100


# ─── 2. _extract_existing_owner_id ─────────────────────────────────────


def test_extract_existing_owner_id_valid() -> None:
    """Cookie standard — valeur extraite verbatim."""
    header = f"{OWNER_COOKIE_NAME}=abc123def456abc123def456abc123ef"
    extracted = _extract_existing_owner_id(header)
    assert extracted == "abc123def456abc123def456abc123ef"


def test_extract_existing_owner_id_with_other_cookies() -> None:
    """Cookie cohabite avec d'autres cookies — extraction ciblée OK."""
    header = f"sessionid=xyz; {OWNER_COOKIE_NAME}=stable123stable123stable123stable; csrftoken=abc"
    extracted = _extract_existing_owner_id(header)
    assert extracted == "stable123stable123stable123stable"


def test_extract_existing_owner_id_absent() -> None:
    """Pas de cookie ``genial_owner_id`` dans le header."""
    extracted = _extract_existing_owner_id("sessionid=xyz; csrftoken=abc")
    assert extracted is None


def test_extract_existing_owner_id_empty_header() -> None:
    """Header vide."""
    assert _extract_existing_owner_id("") is None


def test_extract_existing_owner_id_too_short() -> None:
    """Valeur trop courte — refuse (regex ``{8,64}``)."""
    header = f"{OWNER_COOKIE_NAME}=abc"
    assert _extract_existing_owner_id(header) is None


def test_extract_existing_owner_id_malformed_chars() -> None:
    """Caractères suspects — refuse."""
    header = f"{OWNER_COOKIE_NAME}=<script>alert(1)</script>"
    assert _extract_existing_owner_id(header) is None


# ─── 3. _inject_cookie_into_request_headers ──────────────────────────


def test_inject_when_no_cookie_header() -> None:
    """Pas de header ``Cookie`` du tout — append."""
    headers = [(b"host", b"example.com"), (b"user-agent", b"pytest")]
    result = _inject_cookie_into_request_headers(headers, "abc123def456")
    assert (b"cookie", b"genial_owner_id=abc123def456") in result
    # Les autres headers sont préservés.
    assert (b"host", b"example.com") in result
    assert (b"user-agent", b"pytest") in result


def test_inject_when_cookie_header_has_other_cookies() -> None:
    """Header ``Cookie`` existe avec d'autres cookies — append au header
    cookie en gardant les autres cookies."""
    headers = [
        (b"host", b"example.com"),
        (b"cookie", b"sessionid=xyz; csrftoken=abc"),
    ]
    result = _inject_cookie_into_request_headers(headers, "newowner1234567890")
    cookie_pairs = [v for k, v in result if k == b"cookie"]
    assert len(cookie_pairs) == 1
    decoded = cookie_pairs[0].decode("ascii")
    # Les cookies existants doivent être préservés.
    assert "sessionid=xyz" in decoded
    assert "csrftoken=abc" in decoded
    # Et le nouveau owner_id ajouté.
    assert "genial_owner_id=newowner1234567890" in decoded


def test_inject_when_owner_cookie_already_malformed_in_header() -> None:
    """Cas pathologique : ``genial_owner_id`` est dans le header mais
    avec une valeur malformée. On doit le **remplacer**, pas dupliquer.
    """
    headers = [
        (b"cookie", b"genial_owner_id=BAD; sessionid=xyz"),
    ]
    result = _inject_cookie_into_request_headers(headers, "validowner123456789012345678")
    cookie_pairs = [v for k, v in result if k == b"cookie"]
    assert len(cookie_pairs) == 1
    decoded = cookie_pairs[0].decode("ascii")
    # L'ancienne valeur malformée doit avoir disparu.
    assert "genial_owner_id=BAD" not in decoded
    # La nouvelle valeur doit être présente une seule fois.
    assert decoded.count("genial_owner_id=validowner123456789012345678") == 1
    # L'autre cookie est préservé.
    assert "sessionid=xyz" in decoded


def test_inject_does_not_mutate_input() -> None:
    """Le middleware ne mute pas la liste de headers en entrée — il
    retourne une nouvelle liste."""
    headers = [(b"host", b"example.com")]
    snapshot = list(headers)
    _ = _inject_cookie_into_request_headers(headers, "abc123def456")
    assert headers == snapshot


# ─── 4. Dispatch end-to-end via TestClient ────────────────────────────


def _build_test_app() -> Starlette:
    """App Starlette minimale qui expose ``GET /probe`` et renvoie le
    contenu du header ``Cookie`` que le middleware lui transmet — c'est
    ce qui permet de vérifier que l'injection a bien eu lieu en amont."""

    async def probe(request: Request) -> JSONResponse:
        return JSONResponse({"cookie_header": request.headers.get("cookie", "")})

    app = Starlette(routes=[Route("/probe", probe)])
    app.add_middleware(BaseHTTPMiddleware, dispatch=ensure_owner_cookie_dispatch)
    return app


def test_dispatch_no_cookie_mints_and_sets_cookie() -> None:
    """Pas de cookie côté client → middleware génère + injecte + pose
    Set-Cookie en réponse."""
    client = TestClient(_build_test_app())
    response = client.get("/probe")
    assert response.status_code == 200
    # Le handler a vu un cookie injecté.
    cookie_header = response.json()["cookie_header"]
    assert OWNER_COOKIE_NAME in cookie_header
    match = re.search(
        rf"{OWNER_COOKIE_NAME}=([0-9a-f]{{32}})",
        cookie_header,
    )
    assert match is not None
    minted = match.group(1)
    # Le response contient un Set-Cookie aligné.
    assert OWNER_COOKIE_NAME in response.headers.get("set-cookie", "")
    assert minted in response.headers.get("set-cookie", "")


def test_dispatch_cookie_present_passes_through() -> None:
    """Cookie déjà présent → middleware n'intervient pas, pas de
    Set-Cookie superflu."""
    client = TestClient(_build_test_app())
    existing = "f" * 32
    response = client.get("/probe", cookies={OWNER_COOKIE_NAME: existing})
    assert response.status_code == 200
    # Le handler voit bien le cookie original.
    assert existing in response.json()["cookie_header"]
    # Pas de Set-Cookie superflu (le middleware ne mint pas si présent).
    set_cookie = response.headers.get("set-cookie", "")
    assert OWNER_COOKIE_NAME not in set_cookie


def test_dispatch_malformed_cookie_is_replaced() -> None:
    """Cookie présent mais malformé → traité comme absent : un nouveau
    est minté et posé en réponse."""
    client = TestClient(_build_test_app())
    response = client.get("/probe", cookies={OWNER_COOKIE_NAME: "BAD"})
    assert response.status_code == 200
    # Le handler voit un nouveau cookie valide (32 hex), pas ``BAD``.
    cookie_header = response.json()["cookie_header"]
    match = re.search(
        rf"{OWNER_COOKIE_NAME}=([0-9a-f]{{32}})",
        cookie_header,
    )
    assert match is not None
    # Set-Cookie aligné.
    assert OWNER_COOKIE_NAME in response.headers.get("set-cookie", "")


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_dispatch_secure_flag_only_on_https(scheme: str) -> None:
    """Le ``Secure`` flag est posé uniquement en HTTPS — sinon Chrome
    refuserait le cookie en dev local."""
    client = TestClient(_build_test_app(), base_url=f"{scheme}://testserver")
    response = client.get("/probe")
    set_cookie = response.headers.get("set-cookie", "")
    if scheme == "https":
        assert "Secure" in set_cookie or "secure" in set_cookie
    else:
        assert "Secure" not in set_cookie and "secure" not in set_cookie
