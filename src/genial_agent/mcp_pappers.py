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

Corrections post-review S02 (cf. phase 3) :

- **B2** healthcheck contract (``tools_count`` toujours présent).
- **C1** cap total wall-clock sur ``call_tool`` (``CALL_TOOL_BUDGET_S``).
- **C2** heuristique crédits passée en regex à frontière de mot.
- **C3** single-flight délégué au cache (cf. ``mcp_cache.py``).
- **C4** résolution ``_is_degraded`` mémoïsée à module-load.
- **C6** retry étendu à ``McpError`` / ``anyio`` / ``asyncio.TimeoutError``.
- **C7** sérialisation ``args`` tolérante côté cache.
- **R7** parcours de ``content`` pour trouver le 1er bloc texte.
- **M2** base commune ``PappersError``.
- **M3** fallback description explicite.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import anyio
import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import StreamableHTTPError, streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client
from mcp.shared.exceptions import McpError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    stop_after_delay,
    wait_exponential_jitter,
)

from genial_agent.config import settings
from genial_agent.mcp_cache import cache

logger = structlog.get_logger(__name__)

PAPPERS_BASE_URL = "https://mcp.pappers.fr"


# S09.6 — Workaround tool-level pour le bug PAYG côté serveur Pappers
# (cf. docs/pappers-mcp.md §4.2). Quand un tool listé ici lève
# ``CreditsExhausted``, ``call_tool`` n'élève pas l'exception : il
# retourne un ``tool_result`` synthétisé avec ``isError=True`` + un hint
# que l'agent peut interpréter pour rebondir vers un tool alternatif.
# Trade-off explicité dans la story §"Architecture phase 1 — Axe 2" :
# l'agent garde la décision (le hint est une suggestion), mais reçoit
# l'info nécessaire au rebond. Retrait facile (~15 lignes) le jour où
# Pappers fixe le bug — cf. trigger S09.8 dans traces/S095_iterations.md.
WORKAROUND_HINTS: dict[str, str] = {
    "comptes-entreprise": (
        "Tool `comptes-entreprise` indisponible (crédits abo Pappers "
        "épuisés + bug PAYG côté serveur, cf. docs/pappers-mcp.md §4.2). "
        "Tools alternatifs côté Pappers (à la disposition de l'agent "
        "selon la question posée) : "
        "`recherche-entreprises` (filtre par `siren` + `return_fields` "
        "parmi `chiffre_affaires`, `resultat`, `capital`, `effectif`, "
        "`annee_finances`, `annee_effectif` — couvre les chiffres "
        "headline d'une année récente, 1 crédit) ; "
        "`cartographie-entreprise` (filiales et liens groupe). "
        "Aucune alternative ne couvre les comptes annuels détaillés "
        "multi-années."
    ),
}

# Budgets côté client. Le wall-clock total d'un ``call_tool`` (retry +
# réseau + handshake) ne doit pas dépasser ``CALL_TOOL_BUDGET_S`` pour
# respecter l'objectif UX < 6 s médian (cahier §4) tout en laissant une
# marge pour les 1res requêtes à froid. Le split connect/read permet
# d'échouer raisonnablement vite sur backend injoignable.
CONNECT_TIMEOUT_S = 10  # TLS + DNS + 1st packet ; constaté ~1-3 s live
READ_TIMEOUT_S = 15
DEFAULT_READ_TIMEOUT = timedelta(seconds=READ_TIMEOUT_S)
CALL_TOOL_BUDGET_S = 20  # wall-clock max d'un call_tool, y compris retries


def _build_http_client() -> httpx.AsyncClient:
    """Build an httpx.AsyncClient with our default MCP timeouts.

    Le SDK MCP 1.27 bascule sur ``streamable_http_client`` (sans
    deprecation) et délègue les timeouts à l'httpx client injecté.
    Split connect/read : on échoue vite sur backend injoignable, on
    tolère des reads un peu plus longs pour laisser Pappers cruncher.
    """
    return create_mcp_http_client(
        timeout=httpx.Timeout(
            READ_TIMEOUT_S,
            connect=CONNECT_TIMEOUT_S,
            read=READ_TIMEOUT_S,
        ),
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


class PappersError(RuntimeError):
    """Base commune des erreurs métier Pappers. Permet à S03 de catcher
    ``PappersError`` pour tomber en mode fallback sans distinguer
    crédits vs erreur générique quand la distinction n'importe pas."""


class CreditsExhausted(PappersError):
    """Levé quand le cap crédits journalier est atteint et que le cache
    ne contient pas la réponse demandée. Intercepté par l'agent (S03) qui
    doit rendre un message utilisateur explicite (cf. cahier §16.3).

    Également levé quand Pappers refuse l'exécution d'un outil pour
    cause de crédits insuffisants (message encodé dans
    ``content[0].text``, cf. ``_extract_business_error``)."""


class PappersToolError(PappersError):
    """Erreur métier renvoyée par Pappers dans ``content[0].text`` sous
    la forme ``{"error": "..."}``. Contrairement à ``isError=True`` du
    protocole MCP, ce format est Pappers-spécifique et doit être
    détecté explicitement. **Non caché** pour éviter d'empoisonner le
    cache avec une erreur (le prochain appel identique retente)."""

    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(f"{tool_name}: {message}")
        self.tool_name = tool_name
        self.message = message


def _first_text_block(content: Any) -> str | None:
    """Retourne le texte du premier bloc MCP de type 'text' (ou
    présumé texte). Tolère un content vide ou des blocs non-texte en
    tête (metadata, images) — cf. review S02 R7."""
    if not isinstance(content, list):
        return None
    for block in content:
        if not isinstance(block, dict):
            continue
        # MCP pré-1.10 n'impose pas toujours "type" — on accepte aussi
        # un block qui a un champ texte sans discriminant.
        btype = block.get("type")
        if btype is not None and btype != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            return text
    return None


def _extract_business_error(payload: dict[str, Any]) -> str | None:
    """Détecte le pattern d'erreur métier Pappers encodé dans le texte.

    Pappers renvoie parfois HTTP 200 + ``isError=False`` avec un contenu
    texte JSON ``{"error": "..."}`` — cas constaté pour "crédits
    insuffisants" sur les outils premium. On parse le premier bloc
    texte (pas forcément ``content[0]`` — cf. R7) : si c'est un JSON
    dict contenant la clé ``error``, on retourne la valeur. Sinon
    ``None`` (payload OK).
    """
    # Respecte aussi le flag MCP standard si Pappers le remonte un jour.
    if payload.get("isError"):
        text = _first_text_block(payload.get("content"))
        return text or "MCP isError=True"

    text = _first_text_block(payload.get("content"))
    if text is None:
        return None
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return parsed["error"]
    return None


# Regex à frontière de mot : évite les faux positifs ("discrédit",
# "accrédité", "no credit card required"). Couvre FR/EN + variantes
# plurielles et termes synonymes (quota, jeton, token). cf. review C2.
_CREDITS_PATTERN = re.compile(
    r"\b(cr[ée]dits?|credits?|quotas?|jetons?|tokens?)\b",
    re.IGNORECASE,
)


def _raise_business_error(tool_name: str, message: str) -> None:
    """Lève ``CreditsExhausted`` si le message parle de crédits, sinon
    ``PappersToolError``. Permet à S03/S07 de distinguer un manque de
    crédits (geste utilisateur) d'une autre erreur métier (bug, SIREN
    inconnu…)."""
    if _CREDITS_PATTERN.search(message):
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
    """Tenacity predicate : retry sur 5xx / 429 / erreurs réseau /
    protocole MCP transient — pas sur 4xx auth/not-found.

    Étendu en review C6 : ``McpError``, ``StreamableHTTPError`` (niveau
    protocole), ``anyio.EndOfStream`` / ``anyio.BrokenResourceError``
    (HTTP/2 stream broken), ``asyncio.TimeoutError`` (notre propre
    budget wall-clock) sont désormais retried.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or 500 <= code < 600
    return isinstance(
        exc,
        (
            httpx.TransportError,
            httpx.TimeoutException,
            StreamableHTTPError,
            McpError,
            anyio.EndOfStream,
            anyio.BrokenResourceError,
            asyncio.TimeoutError,
        ),
    )


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
                description=t.description or f"Pappers tool: {t.name}",
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
    ``anthropic.messages.create(tools=[...])`` : ``name``,
    ``description``, ``input_schema``. Consommé par S03.

    Contrainte Anthropic sur ``name`` : ``^[a-zA-Z0-9_-]{1,128}$`` — les
    noms kebab-case Pappers (``recherche-entreprises``) passent tels
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
    stop=(stop_after_attempt(3) | stop_after_delay(CALL_TOOL_BUDGET_S)),
    wait=wait_exponential_jitter(initial=0.5, max=2.0, jitter=0.1),
    retry=retry_if_exception(_is_retryable),
    reraise=True,
)
async def _invoke_tool_live(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Appel réseau brut, enveloppé par le retry tenacity.

    Chaque tentative est en outre bornée par ``CALL_TOOL_BUDGET_S`` via
    ``asyncio.timeout`` — si une tentative dépasse, on échoue en
    ``TimeoutError`` qui déclenche un retry (ou la fin du budget via
    ``stop_after_delay``).
    """
    url = _build_url()
    async with asyncio.timeout(CALL_TOOL_BUDGET_S):
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


# Résolution paresseuse-mais-mémoïsée du predicate de dégradation
# (fourni par S07 via `observability/credit_guard.degraded`). Tant que
# S07 n'est pas mergée, on renvoie toujours ``False``. Après merge,
# l'import est tenté une seule fois (cf. review C4) — un
# ``ImportError`` n'est pas caché par Python dans ``sys.modules`` donc
# on doit le mémoïser manuellement pour éviter le refetch sur chaque
# ``call_tool``.
_degraded_fn: Callable[[], bool] | None = None
_degraded_resolved: bool = False


def _is_degraded() -> bool:
    global _degraded_fn, _degraded_resolved
    if not _degraded_resolved:
        try:
            from genial_agent.observability.credit_guard import degraded

            _degraded_fn = degraded
        except ImportError:
            _degraded_fn = None
        _degraded_resolved = True
    return _degraded_fn() if _degraded_fn else False


def _reset_degraded_cache() -> None:
    """Testing hook : force la prochaine résolution de ``_is_degraded``
    à retenter l'import. Usage exclusivement dans les tests."""
    global _degraded_fn, _degraded_resolved
    _degraded_fn = None
    _degraded_resolved = False


def _build_workaround_tool_result(name: str, exc: CreditsExhausted) -> dict[str, Any] | None:
    """S09.6 (B3) — Si ``name`` a un workaround documenté, retourne un
    ``tool_result`` synthétique ``{isError, content[text]}`` qui contient
    l'erreur originale + un hint exploitable par l'agent. Sinon ``None``.

    Trade-off (cf. story §"Architecture phase 1 — Axe 2") : on
    synthétise une réponse côté code mais on **ne substitue pas** l'appel.
    L'agent garde la décision finale (le hint est suggestif, pas forcé).
    Retrait facile (suppression de la clé dans ``WORKAROUND_HINTS``)
    quand Pappers fixe le bug PAYG.
    """
    hint = WORKAROUND_HINTS.get(name)
    if hint is None:
        return None
    logger.info(
        "pappers_workaround_hint_emitted",
        tool_name=name,
        # Pas de scrubbing nécessaire : ``str(exc)`` contient le message
        # Pappers public (pas de clé). Tronqué à 200 par sécurité.
        original_error=str(exc)[:200],
    )
    return {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"error": str(exc), "workaround_hint": hint},
                    ensure_ascii=False,
                ),
            }
        ],
    }


async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Exécute un tool Pappers avec cache 24 h + retry tenacity +
    single-flight sur cache miss concurrent (cf. review S02 C3).

    Ordre :

    1. Cache hit → retour immédiat, 0 crédit consommé.
    2. Mode dégradé (crédits épuisés, cf. S07) → ne consulte que le
       cache ; si miss, lève ``CreditsExhausted``.
    3. Sinon appel réseau coalescé par single-flight : deux appelants
       simultanés sur la même clé partagent un seul crédit. Retry
       tenacity (3 tentatives, backoff expo 0.5 → 1 → 2 s + jitter,
       cap total ``CALL_TOOL_BUDGET_S``). Pas de retry sur 4xx hors 429
       (cf. ``_is_retryable``).
    4. Si la réponse contient une erreur métier Pappers (texte JSON
       ``{"error": "..."}`` ou ``isError=True``) → **non cachée**,
       lève ``CreditsExhausted`` (si crédits) ou ``PappersToolError``.
    5. Sinon stocke dans le cache et retourne.

    **S09.6 — Workaround crédits insuffisants (B3)** : si
    ``CreditsExhausted`` est levé pour un tool listé dans
    ``WORKAROUND_HINTS`` (étapes 2 ou 4 ci-dessus), l'exception est
    interceptée et remplacée par un ``tool_result`` synthétique avec
    ``isError=True`` + hint exploitable par l'agent. Pour les autres
    tools, ``CreditsExhausted`` est propagé tel quel.
    """
    cached = await cache.get(name, args)
    if cached is not None:
        return cached

    try:
        return await _execute_call_tool(name, args)
    except CreditsExhausted as exc:
        # B3 : remplacer l'exception par un tool_result enrichi pour
        # les tools avec workaround documenté. Couvre les deux chemins
        # de raise CreditsExhausted : mode dégradé (cap journalier) et
        # erreur métier Pappers ("crédits insuffisants" dans le payload).
        synth = _build_workaround_tool_result(name, exc)
        if synth is not None:
            return synth
        raise


async def _execute_call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coeur métier de ``call_tool`` (extrait pour permettre l'enveloppe
    workaround B3 sans imbrication massive de try/except)."""
    if _is_degraded():
        logger.warning("pappers_degraded_cache_miss", tool_name=name)
        raise CreditsExhausted(f"Cap crédits Pappers atteint, cache miss sur {name}")

    started = time.monotonic()

    async def _producer() -> dict[str, Any]:
        return await _invoke_tool_live(name, args)

    try:
        payload = await cache.single_flight(name, args, _producer)
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
            # Scrubbing léger côté S02 (PII scrubbing complet ajouté
            # par S07 §14.4) : on tronque à 200 chars pour limiter
            # l'exposition.
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
    # S07 — instrumentation au call-site succès. Cache hit (return en
    # amont) et erreurs métier (raise) ne passent pas ici, donc on ne
    # double-compte pas. Import tardif pour éviter la dépendance
    # cyclique (observability → guardrails.caps via credit_guard).
    from genial_agent.observability.stats import incr as _stats_incr

    _stats_incr(total_tool_calls=1, pappers_calls_today=1)
    return payload


async def prewarm_cache(
    call: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
) -> None:
    """Préchauffe le cache sur les 3 entités officielles (LVMH, BNP,
    Carrefour) pour que le mode dégradé fonctionne même en sortie de
    boot (cahier §5.4). Appelé une fois depuis ``cl.on_chat_start``
    (S06) ou le script de setup.

    Args:
        call: fonction d'appel à injecter (tests) ; défaut
            ``call_tool``. Le paramètre est uniquement là pour
            faciliter les tests unitaires — production utilise
            ``call_tool`` directement.

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
    caller = call or call_tool
    seeds = ("LVMH", "BNP Paribas", "Carrefour")
    for name in seeds:
        try:
            await caller(
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

    Contrat (review B2) : toutes les clés sont présentes dans les deux
    branches, pour qu'un consumer S07 puisse faire ``result["key"]``
    sans garde ``KeyError``.

    Returns:
        {"status": "ok" | "ko", "latency_ms": int, "tools_count": int,
         "error": str | None}
    """
    started = time.monotonic()
    try:
        tools = await list_available_tools()
    except Exception as exc:  # noqa: BLE001
        latency = int((time.monotonic() - started) * 1000)
        logger.warning(
            "pappers_healthcheck_failed",
            latency_ms=latency,
            error_type=type(exc).__name__,
            # Jamais logguer l'URL ni la clé
        )
        return {
            "status": "ko",
            "latency_ms": latency,
            "tools_count": 0,
            "error": type(exc).__name__,
        }
    latency = int((time.monotonic() - started) * 1000)
    logger.info(
        "pappers_healthcheck_ok",
        latency_ms=latency,
        tools_count=len(tools),
    )
    return {
        "status": "ok",
        "latency_ms": latency,
        "tools_count": len(tools),
        "error": None,
    }
