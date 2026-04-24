"""Client MCP Pappers en transport streamable-http.

Utilise le SDK `mcp>=1.27.0` (PyPI, cf. S01 pyproject.toml) et s'appuie
sur `streamable_http_client` (nouvelle API non-deprecated de 1.27) +
`ClientSession`. Le client ouvre une session courte par appel (on ne
persiste pas de connexion entre deux `call_tool` dans cette itération
MVP — une itération future pourra mutualiser via un singleton asyncio).

Garde-fous Pappers (cf. docs/pappers-mcp.md) :
- Transport streamable-http **uniquement** (STDIO et SSE interdits).
- Clé dans le path URL → ne **jamais** logguer l'URL complète.
- Cache 24 h tool-level pour protéger les crédits (cahier §5.4).
- Filtrage agressif des tools au niveau `RETAINED_TOOLS` pour réduire
  la consommation de contexte (pappers-mcp.md §6).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from genial_agent.config import settings
from genial_agent.mcp_cache import cache

logger = structlog.get_logger(__name__)

PAPPERS_BASE_URL = "https://mcp.pappers.fr"
DEFAULT_TIMEOUT_S = 30
DEFAULT_READ_TIMEOUT = timedelta(seconds=30)


def _build_http_client() -> httpx.AsyncClient:
    """Build an httpx.AsyncClient with our default MCP timeouts.

    Le SDK MCP 1.27 bascule sur `streamable_http_client` (sans
    deprecation) et délègue les timeouts à l'httpx client injecté.
    On fige un timeout global de 30 s, cohérent avec ``DEFAULT_TIMEOUT_S``.
    """
    return create_mcp_http_client(
        timeout=httpx.Timeout(DEFAULT_TIMEOUT_S, read=DEFAULT_TIMEOUT_S),
    )


# Tools retenus après discovery (§6 pappers-mcp.md : minimiser pour
# réduire les tokens et le coût en crédits). Liste figée à partir du
# probe réel du 2026-04-24 contre le MCP Pappers.
#
# Couverture U1-U5 (cahier §3) — version compatible pack API offert
# (100 crédits, sans les tools Premium) :
#   U1 identité        → sirenisateur + recherche-entreprises
#   U2 cartographie    → sirenisateur + recherche-dirigeants
#                        + cartographie-entreprise
#   U3 comparaison     → sirenisateur + comptes-entreprise
#                        + recherche-entreprises
#   U4 recherche       → recherche-entreprises
#   U5 KYC             → conformite-personne-physique
#                        + recherche-beneficiaires
#
# ⚠ ``informations-entreprise`` retiré : outil Premium Pappers, renvoie
# systématiquement "crédits insuffisants" avec le pack 100 crédits
# offert (probe 2026-04-24). Activable dans une itération future si
# l'utilisateur souscrit à un pack supérieur.
RETAINED_TOOLS: set[str] = {
    "sirenisateur",
    "recherche-entreprises",
    "comptes-entreprise",
    "cartographie-entreprise",
    "recherche-dirigeants",
    "conformite-personne-physique",
    "recherche-beneficiaires",
}
# Le reste (question-juridique, recherche-decisions-justice, Pappers
# Immobilier, Pappers Politique, Pappers Territoire…) est hors scope
# MVP et filtré côté agent pour ne pas gonfler le prompt ni encourager
# des appels hors périmètre.


@dataclass(frozen=True)
class PappersTool:
    """Vue simplifiée d'un tool MCP Pappers, prête à être sérialisée
    pour l'API Anthropic Messages."""

    name: str
    description: str
    input_schema: dict[str, Any]  # snake_case = format Anthropic


class CreditsExhausted(RuntimeError):
    """Levé quand le cap crédits journalier est atteint et que le cache
    ne contient pas la réponse demandée. Intercepté par l'agent (S03) qui
    doit rendre un message utilisateur explicite (cf. cahier §16.3).

    Également levé quand Pappers refuse l'exécution d'un outil pour
    cause de crédits insuffisants (message encodé dans
    ``content[0].text``, cf. ``_extract_business_error``)."""


class PappersToolError(RuntimeError):
    """Erreur métier renvoyée par Pappers dans ``content[0].text`` sous
    la forme ``{"error": "..."}``. Contrairement à ``isError=True`` du
    protocole MCP, ce format est Pappers-spécifique et doit être
    détecté explicitement. **Non caché** pour éviter d'empoisonner le
    cache avec une erreur (le prochain appel identique retente)."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"{tool_name}: {message}")
        self.tool_name = tool_name
        self.message = message


def _extract_business_error(payload: dict[str, Any]) -> str | None:
    """Détecte le pattern d'erreur métier Pappers encodé dans le texte.

    Pappers renvoie parfois HTTP 200 + ``isError=False`` avec un contenu
    texte JSON ``{"error": "..."}`` — cas constaté pour "crédits
    insuffisants" sur les outils premium. On parse le premier bloc
    ``content[0].text`` : si c'est un JSON dict contenant la clé
    ``error``, on retourne la valeur. Sinon ``None`` (payload OK).
    """
    # Respecte aussi le flag MCP standard si Pappers le remonte un jour.
    if payload.get("isError"):
        content = payload.get("content") or []
        if content and isinstance(content[0], dict):
            return content[0].get("text") or "MCP isError=True"
        return "MCP isError=True"

    content = payload.get("content") or []
    if not content or not isinstance(content[0], dict):
        return None
    text = content[0].get("text")
    if not isinstance(text, str):
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict) and "error" in parsed and isinstance(parsed["error"], str):
        return parsed["error"]
    return None


_CREDITS_HINTS = ("crédit", "credit", "credits", "crédits")


def _raise_business_error(tool_name: str, message: str) -> None:
    """Lève ``CreditsExhausted`` si le message parle de crédits, sinon
    ``PappersToolError``. Permet à S03/S07 de distinguer un manque de
    crédits (geste utilisateur) d'une autre erreur métier (bug, SIREN
    inconnu…)."""
    lower = message.lower()
    if any(h in lower for h in _CREDITS_HINTS):
        raise CreditsExhausted(f"{tool_name}: {message}")
    raise PappersToolError(tool_name, message)


def _build_url() -> str:
    """Construit l'URL complète côté serveur. **Ne jamais logguer.**

    La clé API est dans le path URL (cf. pappers-mcp.md §2).
    """
    if not settings.PAPPERS_API_KEY:
        raise RuntimeError("PAPPERS_API_KEY not set")
    return f"{PAPPERS_BASE_URL}/{settings.PAPPERS_API_KEY}"


def _is_retryable(exc: BaseException) -> bool:
    """Tenacity predicate : retry sur 5xx / 429 / erreurs réseau, pas sur
    4xx auth/not-found (dont 401, 403, 404)."""
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


async def list_available_tools() -> list[PappersTool]:
    """Discovery des tools exposés par le MCP Pappers.

    Filtre sur ``RETAINED_TOOLS`` avant de retourner. Logue la liste des
    noms (sans URL ni clé).
    """
    url = _build_url()  # jamais logué
    async with (
        _build_http_client() as http_client,
        streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ),
        ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        ) as session,
    ):
        await session.initialize()
        result = await session.list_tools()

    retained: list[PappersTool] = []
    for t in result.tools:
        if t.name not in RETAINED_TOOLS:
            continue
        retained.append(
            PappersTool(
                name=t.name,
                description=t.description or "",
                # camelCase côté MCP → snake_case côté Anthropic.
                # `t.inputSchema` est directement un dict JSON Schema
                # draft 2020-12 valide, pas de massage.
                input_schema=t.inputSchema,
            )
        )

    logger.info(
        "pappers_tools_discovered",
        total=len(result.tools),
        retained=len(retained),
        names=[t.name for t in retained],
    )
    return retained


def to_anthropic_schema(tools: list[PappersTool]) -> list[dict[str, Any]]:
    """Convertit des PappersTool vers la shape attendue par
    `anthropic.messages.create(tools=[...])` : ``name``, ``description``,
    ``input_schema``. Consommé par S03.

    Contrainte Anthropic sur ``name`` : ``^[a-zA-Z0-9_-]{1,128}$`` — les
    noms kebab-case Pappers (``informations-entreprise``) passent tels
    quels.
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=2.0, jitter=0.1),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
async def _invoke_tool_live(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Appel réseau brut, enveloppé par le retry tenacity."""
    url = _build_url()
    async with (
        _build_http_client() as http_client,
        streamable_http_client(url, http_client=http_client) as (
            read_stream,
            write_stream,
            _get_session_id,
        ),
        ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        ) as session,
    ):
        await session.initialize()
        result = await session.call_tool(
            name=name,
            arguments=args,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        )
    # `result` = CallToolResult (pydantic). On sérialise en dict pour le
    # cache et pour l'agent (qui n'importera pas mcp.types).
    return result.model_dump(mode="json")


def _is_degraded() -> bool:
    """Vérifie si le mode dégradé cache-only est actif.

    L'import est local pour éviter une dépendance circulaire S02 ↔ S07 :
    `observability/credit_guard.degraded()` sera ajouté par S07. Avant
    S07, le fallback retourne `False` (jamais dégradé).
    """
    try:
        from genial_agent.observability.credit_guard import degraded
    except ImportError:
        return False
    return degraded()


async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Exécute un tool Pappers avec cache 24 h + retry tenacity.

    Ordre :
    1. Cache hit → retour immédiat, 0 crédit consommé.
    2. Mode dégradé (crédits épuisés, cf. S07) → ne consulte que le
       cache ; si miss, lève ``CreditsExhausted``.
    3. Sinon appel réseau avec retry tenacity (3 tentatives, backoff
       expo 0.5 → 1 → 2 s + jitter). Pas de retry sur 401 / 403 / 404
       (cf. ``_is_retryable``).
    4. Si la réponse contient une erreur métier Pappers (texte JSON
       ``{"error": "..."}`` ou ``isError=True``) → **non cachée**,
       lève ``CreditsExhausted`` (si crédits) ou ``PappersToolError``.
    5. Sinon stocke dans le cache et retourne.
    """
    cached = await cache.get(name, args)
    if cached is not None:
        return cached

    if _is_degraded():
        logger.warning("pappers_degraded_cache_miss", tool_name=name)
        raise CreditsExhausted(f"Cap crédits Pappers atteint, cache miss sur {name}")

    started = time.monotonic()
    try:
        payload = await _invoke_tool_live(name, args)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "pappers_call_http_error",
            tool_name=name,
            status=exc.response.status_code,
            # `.response.url` contient la clé → jamais logger
        )
        raise

    business_error = _extract_business_error(payload)
    if business_error is not None:
        logger.warning(
            "pappers_call_business_error",
            tool_name=name,
            latency_ms=int((time.monotonic() - started) * 1000),
            error_message=business_error[:200],
        )
        _raise_business_error(name, business_error)

    logger.info(
        "pappers_call_ok",
        tool_name=name,
        latency_ms=int((time.monotonic() - started) * 1000),
        is_error=False,
    )
    await cache.set(name, args, payload)
    return payload


async def prewarm_cache() -> None:
    """Préchauffe le cache sur les 3 entités officielles (LVMH, BNP,
    Carrefour) pour que le mode dégradé fonctionne même en sortie de
    boot (cahier §5.4). Appelé une fois depuis ``cl.on_chat_start``
    (S06) ou le script de setup.

    Args ``sirenisateur`` validés contre le ``inputSchema`` réel du MCP
    Pappers (probe 2026-04-24) : required = ``country_code`` +
    ``company_name``.

    On **ne tente pas** ``informations-entreprise`` ici : cet outil
    Pappers est premium et renvoie "crédits insuffisants" sur les packs
    API offerts (100 crédits). Pour le MVP, la fiche identité est
    servie via ``recherche-entreprises`` (non-premium, vérifié live).

    Toute exception isolée est loggée et ignorée — le préchauffage est
    un best-effort, pas un bloquant de démarrage.
    """
    seeds = ("LVMH", "BNP Paribas", "Carrefour")
    for name in seeds:
        try:
            await call_tool(
                "sirenisateur",
                {"company_name": name, "country_code": "FR"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.info(
                "pappers_prewarm_skip",
                seed=name,
                error_type=type(exc).__name__,
            )


async def healthcheck() -> dict[str, Any]:
    """Vérifie la connectivité Pappers sans consommer de crédit
    (``tools/list`` est gratuit côté Pappers).

    Returns:
        {"status": "ok" | "ko", "latency_ms": int, "tools_count": int}
    """
    started = time.monotonic()
    try:
        tools = await list_available_tools()
        latency = int((time.monotonic() - started) * 1000)
        logger.info(
            "pappers_healthcheck_ok",
            latency_ms=latency,
            tools_count=len(tools),
        )
        return {"status": "ok", "latency_ms": latency, "tools_count": len(tools)}
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "pappers_healthcheck_failed",
            error_type=type(exc).__name__,
            # Jamais logguer l'URL ni la clé
        )
        return {"status": "ko", "latency_ms": None, "error": type(exc).__name__}
