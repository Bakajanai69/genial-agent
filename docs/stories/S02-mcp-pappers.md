# S02 — Client MCP Pappers

> **Statut** : ⬜ à faire
> **Durée estimée** : 1 h
> **Parallélisable avec** : —

---

## 📍 Contexte

Brancher proprement notre backend au MCP Pappers. Transport
**streamable-http uniquement** (STDIO et SSE non supportés par Pappers).
Cette story expose une interface Python claire qui sera consommée par
l'agent (S03).

Sources de vérité :
- `docs/pappers-mcp.md` (toutes sections — lecture obligatoire).
- `docs/cahier-des-charges.md` §5.4 (MCP Pappers), §10 Do/Don't.

---

## 🔒 Prérequis

- [x] S01 terminée et approuvée (commit `1d1929b` `review(S01): approved`).
- [x] `.env` local contient `PAPPERS_API_KEY` (déjà rempli pré-S01,
      validé par un handshake MCP réel).
- [x] SDK `mcp>=1.27.0,<2` déjà présent dans `pyproject.toml` +
      `uv.lock` (pin figé par S01).

## 🔑 Inputs utilisateur requis

- [x] `PAPPERS_API_KEY` fournie (fichier `.env`, gitignoré).
- [ ] Solde crédits Pappers ≥ 50 (à vérifier sur
      `moncompte.pappers.fr/credits` avant de lancer les tests
      d'intégration).

## 📡 Résultat du probe réel (2026-04-24)

Handshake validé sur `https://mcp.pappers.fr/$PAPPERS_API_KEY` :

- `serverInfo` : `{"name": "pappers", "version": "1.0.0"}`
- `protocolVersion` : `2024-11-05`
- `capabilities.tools.listChanged` : `true`

Liste brute des **31 tools exposés** — à garder pour référence dans
la phase 2, mais on filtre agressivement (cf. `RETAINED_TOOLS` plus
bas) :

`sirenisateur`, `informations-entreprise`, `recherche-entreprises`,
`comptes-entreprise`, `cartographie-entreprise`, `recherche-dirigeants`,
`conformite-personne-physique`, `question-juridique`,
`details-decision-justice`, `recherche-decisions-justice`,
`recherche-articles-loi`, `details-article-loi`, `sommaire-texte-loi`,
`recherche-textes-loi`, `recherche-parcelles`, `recherche-lieux`,
`lire-documents`, `recherche-documents-politiques`,
`details-document-politique`, `details-dossier-politique`,
`recherche-amendements`, `filtres-amendements`,
`recherche-acteurs-politiques`, `details-acteur-politique`,
`recherche-interventions-politiques`, `recherche-votes`,
`cartographie-politique`, `details-document-territoire`,
`recherche-documents-territoire`, `document-territoire-pdf`,
`recherche-beneficiaires`.

⚠ Les noms utilisent des **tirets** (format kebab-case) — conforme au
pattern Anthropic `[a-zA-Z0-9_-]{1,128}`, à garder tels quels dans
`to_anthropic_schema`.

---

## 🎯 Scope

### Dans le scope

- Module `src/genial_agent/mcp_pappers.py` exposant :
  - Construction d'URL côté serveur (clé jamais exposée).
  - Connexion via transport `streamable-http`.
  - Discovery des tools exposés (`list_tools`).
  - Filtrage applicatif des tools retenus.
  - Fonction `healthcheck()` non destructive.
  - **Exécution de tool call** `call_tool(name, args)` utilisée par
    l'agent (S03).
- **Cache applicatif tool-level** (cahier §5.4) :
  - Clé `(tool_name, sha256(args))`, TTL 24 h.
  - Préchauffe des 3 entités officielles (LVMH, BNP Paribas, Carrefour)
    au premier run pour préserver les crédits en dev.
  - Utilisé aussi par le mode dégradé cache-only (§16.3) quand les
    crédits quotidiens sont épuisés.
- **Retry/backoff `tenacity`** sur les tool calls :
  - 3 tentatives, backoff exponentiel 0.5 → 1 → 2 s, jitter ±20 %.
  - Pas de retry sur 401/403 (auth logique) ni 404 (entité inconnue).
- **Mapping `PappersTool → anthropic tool schema`** (fonction
  `to_anthropic_schema(tools: list[PappersTool]) -> list[dict]`)
  consommée par S03.
- Gestion d'erreur réseau, timeout, auth.
- Logging structuré (sans URL complète).

### Hors scope

- Utilisation par l'agent (S03).
- Cache idempotence (session_id, message) TTL 60 s (S07, couche
  différente, au niveau chat).

---

## 🧭 Phase 1 — Elicitation Agent

### ✅ Conclusions elicitation (2026-04-24)

Recherches effectuées via PyPI + inspection directe du SDK `mcp==1.27.0`
déjà installé dans `.venv` (pinné S01). Résultats figés ci-dessous.

#### SDK MCP Python : package officiel et imports

- **Package PyPI** : [`mcp`](https://pypi.org/project/mcp/) — version
  `1.27.0` sortie le 2026-04-02. Déjà pinné dans `pyproject.toml`
  (`mcp>=1.27.0,<2`). Repo :
  [github.com/modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk).
  Les candidats `mcp-sdk` / `anthropic-mcp` **n'existent pas** ou sont
  des wrappers tiers — on ne les utilise pas.

- **Imports à utiliser** (noms exacts, vérifiés par inspection dans
  `.venv/lib/python3.12/site-packages/mcp/client/streamable_http.py`) :

  ```python
  from mcp import ClientSession
  from mcp.client.streamable_http import streamablehttp_client
  ```

  ⚠ Le symbole correct est `streamablehttp_client` (tout attaché, sans
  underscore entre « streamable » et « http »). Un alias
  `streamable_http_client` (avec underscore) existe mais est
  `@deprecated` — ne pas l'utiliser.

#### Pattern client streamable-http (minimal, validé localement)

```python
from datetime import timedelta
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(url, timeout=30) as (
    read_stream,
    write_stream,
    get_session_id,          # 3-tuple, pas 2 — le callback expose l'id de session HTTP
):
    async with ClientSession(
        read_stream,
        write_stream,
        read_timeout_seconds=timedelta(seconds=30),
    ) as session:
        await session.initialize()                # envoie initialize + notifications/initialized
        tools_result = await session.list_tools() # ListToolsResult
        result = await session.call_tool(
            name="informations-entreprise",
            arguments={"siren": "775670417"},
            read_timeout_seconds=timedelta(seconds=30),
        )                                         # CallToolResult
```

- Signature réelle :
  `streamablehttp_client(url, headers=None, timeout=30, sse_read_timeout=300, terminate_on_close=True, httpx_client_factory=..., auth=None)`.
- `session.initialize()` enchaîne lui-même `initialize` + la notif
  `notifications/initialized` — **pas** à faire à la main. Le
  `mcp-session-id` HTTP est géré en interne par le SDK (header
  `Mcp-Session-Id` propagé tout seul).

#### Format `Tool` renvoyé et mapping Anthropic

Inspecté en live sur `mcp.types.Tool` (Pydantic v2) :

- Attribut Python : `tool.inputSchema` (**camelCase**, pas de alias
  snake_case). `tool.model_dump()` sort aussi `inputSchema`.
- Anthropic v0.97.0 attend `input_schema` (snake_case, `Required` dans
  `anthropic.types.ToolParam`, vérifié par introspection du TypedDict).
  → conversion = simple renommage de clé, pas de massage de contenu.
- Le JSON Schema retourné par Pappers est du **draft 2020-12** valide
  (`type`, `properties`, `required`, `$defs`). Pas besoin de stripper
  `$schema` ni `additionalProperties` — Anthropic les ingère sans
  broncher sur un schéma v2020-12 standard.
- Contrainte Anthropic sur les noms : `^[a-zA-Z0-9_-]{1,128}$`. Les
  tools Pappers kebab-case (`informations-entreprise`) passent tels
  quels.

#### Claude Agent SDK (pour préparer S03)

S01 a déjà tranché : on n'utilise **pas** le package PyPI
`claude-agent-sdk` (wrapper du CLI Claude Code). Le chemin officiel
reste `anthropic` + `mcp` combinés. Concrètement S03 fera :

```python
import anthropic

client = anthropic.AsyncAnthropic()
msg = await client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=4096,
    system=SYSTEM_PROMPT,
    tools=to_anthropic_schema(pappers_tools),   # produit par S02
    messages=conversation,
)
# Puis boucle agentique : si stop_reason == "tool_use",
# route le tool_use_block.name vers mcp_pappers.call_tool(...).
```

Pas de `beta.messages.create(..., mcp_servers=[...])` natif :
cette fonctionnalité concerne les serveurs MCP **hébergés par
Anthropic** (MCP Connector). Pour notre cas self-hosted côté Pappers,
on garde le contrôle via notre propre `call_tool`.

### ✅ Points résolus

#### Timeout du client MCP Python

- **Natif dans le SDK** : `streamablehttp_client(timeout=30)` pour
  `connect + initialize`, `sse_read_timeout=300` pour les streams
  long-lived, et `read_timeout_seconds=timedelta(...)` passable soit à
  `ClientSession(...)` soit à chaque `session.call_tool(...)`.
- **Pas besoin** d'envelopper dans `asyncio.wait_for`. On passe le
  timeout explicitement partout pour éviter la valeur par défaut
  silencieuse.

#### Exception d'auth / codes HTTP

- Le SDK n'expose **pas** de hiérarchie d'exceptions dédiée par code
  HTTP. Il y a juste `StreamableHTTPError(Exception)` pour les erreurs
  de protocole.
- Sous le capot, `httpx.Response.raise_for_status()` est appelé
  (`streamable_http.py` lignes 272, 320, 358, 469) → les 4xx/5xx
  remontent en **`httpx.HTTPStatusError`** portant `.response.status_code`.
  401/403 signifient clé Pappers invalide (Pappers renvoie probablement
  404 sur URL mal formée, à constater en prod). 404 côté SDK est déjà
  géré en interne pour les scénarios « session HTTP expirée » — on ne
  l'interprète **pas** comme « entité inconnue » (Pappers encode les
  erreurs métier dans `CallToolResult.isError`, pas en HTTP).
- Erreur métier tool → `CallToolResult` avec `isError=True` et message
  dans `content[0].text`. À catcher séparément des exceptions HTTP.

#### Shape `inputSchema`

- Directement utilisable : `to_anthropic_schema` lit `tool.inputSchema`
  (attribut Pydantic camelCase) et écrit `input_schema` dans le dict
  sortant. Rien d'autre à transformer.

### 📋 Points résolus côté probe réel (rappel S02 phase 0)

- [x] Format discovery confirmé par probe réel : JSON-RPC 2.0,
      `method=tools/list`, résultat
      `{"tools": [{"name", "title", "description", "inputSchema", "execution"}]}`.
- [x] Tools Pappers réels listés ci-dessus (§ "Résultat du probe") ;
      la liste retenue est dans `RETAINED_TOOLS`.
- [x] Handshake MCP encapsulé par `session.initialize()` (envoi
      `initialize` protocolVersion `2024-11-05` + notif
      `notifications/initialized`). SDK gère aussi le header
      `Mcp-Session-Id` tout seul.

### 🔑 Input utilisateur encore attendu

- [ ] **Solde crédits Pappers ≥ 50** — à vérifier par Lancelot sur
      `moncompte.pappers.fr/credits` avant de lancer
      `make test-integration`. Non bloquant pour la phase 2 (les
      tests unitaires tournent sans clé ; l'intégration skip si
      la clé manque ou si le solde est trop bas).

### Commit phase 1

`story(S02): refine — MCP SDK 1.27 imports, timeout native, httpx auth errors, inputSchema mapping`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/mcp_pappers.py` — client.
- `src/genial_agent/mcp_cache.py` — cache tool-level TTL 24 h.
- `src/genial_agent/config.py` — chargement `.env` centralisé (si non
  déjà fait par S01 phase 1).
- `tests/unit/test_S02_mcp_client.py`
- `tests/unit/test_S02_mcp_cache.py`
- `tests/unit/test_S02_tool_mapping.py`
- `tests/integration/test_S02_pappers_live.py`

### Squelette `mcp_pappers.py`

```python
"""Client MCP Pappers en transport streamable-http.

Utilise le SDK `mcp>=1.27.0` (PyPI, cf. S01 pyproject.toml) et s'appuie
sur `streamablehttp_client` + `ClientSession`. Le client ouvre une
session courte par appel (on ne persiste pas de connexion entre deux
`call_tool` dans cette itération MVP — une itération future pourra
mutualiser via un singleton asyncio).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
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

# Tools retenus après discovery (§6 pappers-mcp.md : minimiser pour
# réduire les tokens et le coût en crédits). Liste figée à partir du
# probe réel du 2026-04-24 contre le MCP Pappers.
#
# Couverture U1-U5 (cahier §3) :
#   U1 identité        → sirenisateur + informations-entreprise
#   U2 cartographie    → sirenisateur + recherche-dirigeants
#                        + cartographie-entreprise
#   U3 comparaison     → sirenisateur + informations-entreprise
#                        + comptes-entreprise
#   U4 recherche       → recherche-entreprises
#   U5 KYC             → conformite-personne-physique
#                        + recherche-beneficiaires
RETAINED_TOOLS: set[str] = {
    "sirenisateur",
    "informations-entreprise",
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
    doit rendre un message utilisateur explicite (cf. cahier §16.3)."""


def _build_url() -> str:
    """Construit l'URL complète côté serveur. Ne jamais logguer.

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
    async with streamablehttp_client(url, timeout=DEFAULT_TIMEOUT_S) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        ) as session:
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
    async with streamablehttp_client(url, timeout=DEFAULT_TIMEOUT_S) as (
        read_stream,
        write_stream,
        _get_session_id,
    ):
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=DEFAULT_READ_TIMEOUT,
        ) as session:
            await session.initialize()
            result = await session.call_tool(
                name=name,
                arguments=args,
                read_timeout_seconds=DEFAULT_READ_TIMEOUT,
            )
    # `result` = CallToolResult (pydantic). On sérialise en dict pour le
    # cache et pour l'agent (qui n'importera pas mcp.types).
    return result.model_dump(mode="json")


async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Exécute un tool Pappers avec cache 24 h + retry tenacity.

    Ordre :
    1. Cache hit → retour immédiat, 0 crédit consommé.
    2. Mode dégradé (crédits épuisés, cf. S07) → ne consulte que le
       cache ; si miss, lève ``CreditsExhausted``.
    3. Sinon appel réseau avec retry tenacity (3 tentatives, backoff
       expo 0.5 → 1 → 2 s + jitter). Pas de retry sur 401 / 403 / 404
       (cf. ``_is_retryable``).
    4. Stocke dans le cache et retourne.
    """
    cached = await cache.get(name, args)
    if cached is not None:
        return cached

    # L'import se fait ici pour éviter une dépendance circulaire
    # S02 ↔ S07 (credit_guard arrive plus tard).
    try:
        from genial_agent.observability.credit_guard import degraded
    except ImportError:
        degraded = lambda: False  # noqa: E731 — avant S07

    if degraded():
        logger.warning("pappers_degraded_cache_miss", tool_name=name)
        raise CreditsExhausted(
            f"Cap crédits Pappers atteint, cache miss sur {name}"
        )

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
    logger.info(
        "pappers_call_ok",
        tool_name=name,
        latency_ms=int((time.monotonic() - started) * 1000),
        is_error=bool(payload.get("isError")),
    )
    await cache.set(name, args, payload)
    return payload


async def prewarm_cache() -> None:
    """Préchauffe le cache sur les 3 entités officielles (LVMH, BNP,
    Carrefour) pour que le mode dégradé fonctionne même en sortie de
    boot (cahier §5.4). Appelé une fois depuis ``cl.on_chat_start``
    (S06) ou le script de setup.

    Stratégie : tenter ``sirenisateur`` (résolution nom→SIREN) sur chaque
    entité, puis ``informations-entreprise`` sur le SIREN obtenu. Toute
    exception isolée est loggée et ignorée — le préchauffage est un
    best-effort, pas un bloquant de démarrage.
    """
    seeds = ("LVMH", "BNP Paribas", "Carrefour")
    for name in seeds:
        try:
            siren_res = await call_tool("sirenisateur", {"query": name})
            # Le schéma exact de la réponse dépend de Pappers — on
            # extrait le premier SIREN trouvé (à adapter après probe
            # réel en phase 2).
            structured = siren_res.get("structuredContent") or {}
            siren = None
            for item in structured.get("results", []) or []:
                if item.get("siren"):
                    siren = item["siren"]
                    break
            if siren:
                await call_tool("informations-entreprise", {"siren": siren})
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
```

> **Remarque S07** : `observability/credit_guard.degraded()` n'existe pas
> encore (créé en S07). L'import est volontairement dans un `try/except`
> pour que `mcp_pappers` fonctionne avant S07. Dès S07 mergée, l'import
> devient direct et le fallback `lambda` peut être retiré (code-review
> à prévoir en S07).

### Squelette `mcp_cache.py`

```python
"""Cache tool-level MCP Pappers : (tool_name, args_hash) → result, TTL 24 h.

Couvre deux besoins :
- Protéger les crédits Pappers en dev (itérations rapides sur LVMH/BNP/Carrefour).
- Support du mode dégradé cache-only quand le cap crédits journalier est
  atteint (cf. S07 + cahier §17.2).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TTL_S = 24 * 3600  # 24 h, cahier §5.4


@dataclass
class _Entry:
    value: dict[str, Any]
    expires_at: float


class ToolCache:
    def __init__(self, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._ttl = ttl_s
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(tool_name: str, args: dict[str, Any]) -> str:
        args_canon = json.dumps(args, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(args_canon.encode()).hexdigest()[:16]
        return f"{tool_name}:{h}"

    async def get(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        k = self.key(tool_name, args)
        async with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[k]
                return None
            logger.info("mcp_cache_hit", tool_name=tool_name)
            return entry.value

    async def set(self, tool_name: str, args: dict[str, Any], value: dict[str, Any]) -> None:
        k = self.key(tool_name, args)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)

    async def contains(self, tool_name: str, args: dict[str, Any]) -> bool:
        return (await self.get(tool_name, args)) is not None


cache = ToolCache()
```

### `config.py` (si manquant)

```python
"""Chargement centralisé des variables d'environnement."""
from __future__ import annotations
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    PAPPERS_API_KEY: str = os.getenv("PAPPERS_API_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    ENABLE_VOICE_BRIEF: bool = os.getenv("ENABLE_VOICE_BRIEF", "false").lower() == "true"


settings = Settings()
```

### APIs à utiliser (endpoints réels)

- `https://mcp.pappers.fr/{PAPPERS_API_KEY}` — transport streamable-http
  uniquement (cf. `pappers-mcp.md` §3).
- Méthodes MCP standards consommées : `initialize`, `notifications/initialized`
  (enchaînées par `session.initialize()`), `tools/list`, `tools/call`.

### Gotchas documentés

- **Ne jamais logguer `_build_url()`** → fuite de clé. Idem pour
  `httpx.HTTPStatusError.response.url` qui contient le path complet.
  Logger uniquement `status_code` et `type(exc).__name__`.
- **Transport SSE interdit par Pappers** : ne pas utiliser
  `mcp.client.sse`. Seul `mcp.client.streamable_http.streamablehttp_client`
  est supporté (cf. `pappers-mcp.md` §3).
- **Nom d'import exact** : `streamablehttp_client` (tout attaché). Un
  alias `streamable_http_client` (avec underscore) existe mais est
  `@deprecated` dans `mcp 1.27` — ne pas l'utiliser.
- **Context manager 3-tuple** : `streamablehttp_client(...)` yield
  `(read_stream, write_stream, get_session_id_callback)`, **pas** un
  tuple à 2. Les exemples en ligne plus anciens montrent parfois 2
  éléments → outdated.
- **Pas de `asyncio.wait_for` en surcouche** : timeout natif via
  `streamablehttp_client(timeout=30)` + `read_timeout_seconds=timedelta(...)`
  sur `ClientSession` et `call_tool`. Empiler `wait_for` par-dessus
  masque les vraies exceptions du SDK et complique le diagnostic.
- **Handshake géré par `session.initialize()`** : inutile d'envoyer
  `notifications/initialized` à la main — le SDK le fait. Idem pour le
  header `Mcp-Session-Id` (propagation automatique).
- **Exception d'auth / HTTP** : aucune classe dédiée dans `mcp 1.27`.
  Le SDK fait `response.raise_for_status()` → `httpx.HTTPStatusError`
  avec `.response.status_code` pour 401/403/429/5xx. 404 est parfois
  intercepté côté SDK pour le cas « session HTTP expirée », ne pas
  l'interpréter comme « entité inconnue ».
- **Erreurs métier tool** : Pappers n'utilise pas le status HTTP pour
  signaler « SIREN introuvable » → réponse 200 OK avec
  `CallToolResult(isError=True, content=[TextContent(text="...")])`.
  À catcher séparément (`payload["isError"]`) dans le code appelant
  (S03).
- **Retry tenacity** : predicate `_is_retryable` ne retry **que** sur
  429 / 5xx / `httpx.TransportError` / `httpx.TimeoutException`. 401,
  403, 404 passent immédiatement en échec, sans 3 tentatives.
- **Canonicalisation des args du cache** : `json.dumps(args,
  sort_keys=True, separators=(",", ":"))` sinon `{"a":1,"b":2}` et
  `{"b":2,"a":1}` donnent des hash différents et manquent le cache.
- **Mapping `inputSchema` → `input_schema`** : `mcp.types.Tool` est
  Pydantic v2, l'attribut Python est **bien camelCase** `t.inputSchema`
  (pas d'alias snake_case). Anthropic v0.97 attend `input_schema`
  (vérifié sur `anthropic.types.ToolParam`). Simple renommage de clé
  dans `to_anthropic_schema`, pas de strip `$schema` /
  `additionalProperties` (Anthropic accepte le JSON Schema v2020-12
  complet).
- **Sérialisation `CallToolResult`** : `result.model_dump(mode="json")`
  pour garder les types compatibles cache (dict sérialisable). Sans
  `mode="json"`, certains champs (timestamps, enums) restent en
  objets Python non-pickle-safe.

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S02_mcp_client.py
from __future__ import annotations

import httpx
import pytest

from genial_agent import mcp_pappers


def test_build_url_uses_env_key(monkeypatch):
    # `settings` est un dataclass frozen → on patche via l'accès module-level.
    monkeypatch.setattr(mcp_pappers.settings, "PAPPERS_API_KEY", "abc123")
    url = mcp_pappers._build_url()
    assert url == "https://mcp.pappers.fr/abc123"


def test_build_url_fails_if_no_key(monkeypatch):
    monkeypatch.setattr(mcp_pappers.settings, "PAPPERS_API_KEY", "")
    with pytest.raises(RuntimeError, match="PAPPERS_API_KEY"):
        mcp_pappers._build_url()


def test_retained_tools_is_set():
    assert isinstance(mcp_pappers.RETAINED_TOOLS, set)
    assert {"sirenisateur", "informations-entreprise"} <= mcp_pappers.RETAINED_TOOLS


def test_is_retryable_401_403_404_not_retried():
    # Dummy response objects
    for code in (401, 403, 404):
        resp = httpx.Response(code, request=httpx.Request("GET", "http://x"))
        err = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
        assert mcp_pappers._is_retryable(err) is False


def test_is_retryable_429_and_5xx_are_retried():
    for code in (429, 500, 502, 503, 504):
        resp = httpx.Response(code, request=httpx.Request("GET", "http://x"))
        err = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
        assert mcp_pappers._is_retryable(err) is True


def test_is_retryable_transport_and_timeout():
    assert mcp_pappers._is_retryable(httpx.ConnectError("down")) is True
    assert mcp_pappers._is_retryable(httpx.ReadTimeout("slow")) is True
    # Exception non liée → pas de retry
    assert mcp_pappers._is_retryable(ValueError("nope")) is False


def test_api_key_never_in_str_representation(monkeypatch):
    """Aucune repr du module/classes ne doit contenir la clé — garde-fou
    contre un `repr(settings)` qui fuiterait dans un traceback."""
    monkeypatch.setattr(mcp_pappers.settings, "PAPPERS_API_KEY", "SECRETKEY_X1")
    # On ne touche pas à settings.__repr__ directement (dataclass standard
    # expose la clé) — la règle est : ne jamais logger/afficher `settings`.
    # Ce test fige la règle côté module : pas de constante qui recopie la clé.
    for attr_name in dir(mcp_pappers):
        if attr_name.startswith("_"):
            continue
        val = getattr(mcp_pappers, attr_name)
        if isinstance(val, str):
            assert "SECRETKEY_X1" not in val
```

```python
# tests/unit/test_S02_mcp_cache.py
import pytest
from genial_agent.mcp_cache import ToolCache


async def test_cache_hit():
    c = ToolCache(ttl_s=60)
    await c.set("get_company", {"siren": "775670417"}, {"name": "LVMH"})
    res = await c.get("get_company", {"siren": "775670417"})
    assert res == {"name": "LVMH"}


async def test_cache_args_canonical_order():
    c = ToolCache(ttl_s=60)
    await c.set("foo", {"a": 1, "b": 2}, {"ok": True})
    res = await c.get("foo", {"b": 2, "a": 1})
    assert res == {"ok": True}


async def test_cache_expires():
    c = ToolCache(ttl_s=0)
    await c.set("foo", {"x": 1}, {"v": 1})
    import asyncio
    await asyncio.sleep(0.01)
    assert await c.get("foo", {"x": 1}) is None
```

```python
# tests/unit/test_S02_tool_mapping.py
from __future__ import annotations

import re

from mcp.types import Tool as McpTool

from genial_agent.mcp_pappers import PappersTool, to_anthropic_schema


def test_mapping_minimal_shape():
    tools = [
        PappersTool(
            name="informations-entreprise",
            description="Get company info by SIREN",
            input_schema={
                "type": "object",
                "properties": {"siren": {"type": "string"}},
                "required": ["siren"],
            },
        )
    ]
    out = to_anthropic_schema(tools)
    assert out == [
        {
            "name": "informations-entreprise",
            "description": "Get company info by SIREN",
            "input_schema": {
                "type": "object",
                "properties": {"siren": {"type": "string"}},
                "required": ["siren"],
            },
        }
    ]


def test_anthropic_tool_name_pattern():
    """Tous les noms Pappers kebab-case doivent matcher le pattern
    Anthropic `^[a-zA-Z0-9_-]{1,128}$`."""
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
    from genial_agent.mcp_pappers import RETAINED_TOOLS

    for name in RETAINED_TOOLS:
        assert pattern.match(name), f"Tool name invalide pour Anthropic: {name!r}"


def test_camel_case_input_schema_bridge():
    """Vérifie que la clé passe bien de camelCase (spec MCP) à
    snake_case (Anthropic) sans écrasement du contenu JSON Schema."""
    mcp_tool = McpTool(
        name="sirenisateur",
        description="Trouve un SIREN depuis un nom",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    # Conversion (simule `list_available_tools`)
    pt = PappersTool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        input_schema=mcp_tool.inputSchema,
    )
    [out] = to_anthropic_schema([pt])
    assert "input_schema" in out
    assert "inputSchema" not in out
    # Le $schema est conservé, Anthropic l'accepte sans strip.
    assert out["input_schema"]["$schema"].endswith("2020-12/schema")
```

#### Intégration (réel, skip si pas de clé)

```python
# tests/integration/test_S02_pappers_live.py
from __future__ import annotations

import os

import pytest

from genial_agent import mcp_pappers

pytestmark = pytest.mark.integration

SKIP_REASON = "PAPPERS_API_KEY not set"


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_healthcheck_live():
    result = await mcp_pappers.healthcheck()
    assert result["status"] == "ok"
    assert result["tools_count"] > 0


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_tools_discovery_includes_retained_core():
    """Le probe réel du 2026-04-24 montre que `sirenisateur` et
    `informations-entreprise` sont exposés — s'ils disparaissent,
    la démo U1 casse."""
    tools = await mcp_pappers.list_available_tools()
    names = {t.name for t in tools}
    assert "sirenisateur" in names
    assert "informations-entreprise" in names


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_call_tool_lvmh_then_cache_hit():
    """Deux `call_tool` identiques rapprochés → le 2ᵉ est servi par le
    cache (vérifiable par l'absence de log `pappers_call_ok` au second
    appel, et par l'invariant temporel : 2ᵉ appel < 50 ms)."""
    import time

    args = {"siren": "775670417"}  # LVMH
    t0 = time.monotonic()
    r1 = await mcp_pappers.call_tool("informations-entreprise", args)
    t1 = time.monotonic()
    r2 = await mcp_pappers.call_tool("informations-entreprise", args)
    t2 = time.monotonic()

    assert r1 == r2
    assert (t2 - t1) < 0.05, "2e appel aurait dû être un cache hit instantané"
    # Le 1er appel doit être lent par rapport au 2e (sanity check)
    assert (t1 - t0) > (t2 - t1)


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_anthropic_schema_payload_is_usable():
    """Sanity : le payload produit est bien accepté comme `tools`
    param par la validation côté anthropic TypedDict (clés ok)."""
    tools = await mcp_pappers.list_available_tools()
    schema = mcp_pappers.to_anthropic_schema(tools)
    for t in schema:
        assert set(t.keys()) == {"name", "description", "input_schema"}
        assert isinstance(t["input_schema"], dict)
        assert t["input_schema"].get("type") == "object"
```

### Commandes de vérification

```bash
make lint
make test-unit
PAPPERS_API_KEY=xxx make test-integration
```

### Commit phase 2

`feat(S02): Pappers MCP client with cache 24h, tenacity retry, anthropic schema mapping`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] Aucun `print(url)` / `logger.info(url=...)` / f-string qui leak
      la clé. Grep actif : `grep -RiE "mcp.pappers.fr/" src/ tests/`
      ne doit retourner que la constante `PAPPERS_BASE_URL`.
- [ ] `grep -r "PAPPERS_API_KEY" src/` ne retourne que `config.py`.
- [ ] Aucun `logger.*(response.url)` ou `exc.response.url` — `httpx`
      inclut la clé dans l'URL de la requête.
- [ ] `healthcheck()` renvoie `{"status": "ko", ...}` sur timeout /
      erreur réseau / auth failed, sans lever d'exception vers
      l'appelant.
- [ ] Import utilisé : `from mcp.client.streamable_http import
      streamablehttp_client` (pas l'alias `streamable_http_client`
      `@deprecated`).
- [ ] Tests d'intégration skip proprement si clé absente (`pytest -v`
      doit afficher `SKIPPED [reason='PAPPERS_API_KEY not set']`).
- [ ] `list_available_tools()` logue `names=[...]` + counts, **pas
      d'URL**.
- [ ] Cache : `test_cache_args_canonical_order` vert + `test_cache_hit`
      vert (clé = `(tool_name, sha256(json.dumps(args, sort_keys)))`).
- [ ] Retry tenacity : `test_is_retryable_401_403_404_not_retried`
      vert — vérifie que 401/403/404 sortent au 1er essai, sans 3
      tentatives.
- [ ] `to_anthropic_schema` : keys exactement
      `{"name", "description", "input_schema"}`, aucune clé camelCase.
- [ ] `prewarm_cache` implémenté, best-effort (exception par seed →
      log info + continue, pas de crash au boot).
- [ ] `call_tool` : le retour est bien un `dict` JSON-sérialisable
      (i.e. `result.model_dump(mode="json")`), pas un objet Pydantic
      — requis pour le cache et pour S03.
- [ ] `CreditsExhausted` importable depuis `genial_agent.mcp_pappers`
      (consommé par S03 / S07).

### Commit phase 3

`review(S02): approved`

---

## ✅ Critères d'acceptation

- [ ] `healthcheck()` retourne `{"status": "ok", ...}` avec une vraie clé.
- [ ] `list_available_tools()` logue `total` ET `retained` ≥ 2 au
      premier run, ainsi que la liste des noms (sans URL).
- [ ] Tous les tests unitaires passent **sans clé API**
      (`make test-unit` vert).
- [ ] Les tests d'intégration passent **avec une vraie clé**
      (`make test-integration` vert).
- [ ] Deux appels rapprochés à
      `call_tool("informations-entreprise", {"siren": "775670417"})`
      → 1 crédit consommé, 2ᵉ appel < 50 ms (cache hit instantané).
- [ ] `to_anthropic_schema` retourne un payload directement utilisable
      par `anthropic.messages.create(tools=...)` : clés `name`,
      `description`, `input_schema` uniquement.
- [ ] Retry tenacity : un 401/403/404 simulé **ne déclenche pas** 3
      tentatives (test unitaire vert).
- [ ] `gitleaks detect` clean sur le commit phase 2.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S02 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
