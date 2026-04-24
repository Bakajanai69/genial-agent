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

- [ ] S01 terminée et approuvée.
- [x] `.env` local contient `PAPPERS_API_KEY` (déjà rempli pré-S01,
      validé par un handshake MCP réel).

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

### Recherche en ligne à effectuer

- [ ] **Librairie MCP Python** : nom exact du package, version actuelle,
      API pour transport `streamable-http`. Candidats : `mcp`,
      `mcp-sdk`, `anthropic-mcp`. Confirmer le package officiel 2026.
- [ ] Lire https://modelcontextprotocol.io (ou équivalent actuel) pour
      le pattern client Python en streamable-http.
- [ ] Vérifier comment le Claude Agent SDK Python 2026 consomme un
      serveur MCP distant (pour préparer S03).
- [x] Format discovery confirmé par probe réel : JSON-RPC 2.0,
      `method=tools/list`, résultat
      `{"tools": [{"name", "title", "description", "inputSchema", "execution"}]}`.
- [x] Tools Pappers réels listés ci-dessus (§ "Résultat du probe") ;
      la liste retenue est dans `RETAINED_TOOLS`.
- [x] Handshake requis confirmé :
      1. `POST` avec `method=initialize` (protocolVersion `2024-11-05`).
      2. `POST` avec `method=notifications/initialized` (notification).
      3. Les autres appels peuvent ensuite être envoyés (le serveur
         Pappers ne semble pas exiger de `mcp-session-id` persistant
         côté HTTP, mais le SDK MCP Python le gère probablement
         automatiquement — à confirmer).

### Points à résoudre

- [ ] Le client MCP Python a-t-il un timeout natif ? Sinon, imposer un
      timeout par appel via `asyncio.wait_for` (30 s par défaut).
- [ ] Comportement en cas d'échec d'auth (clé invalide) : type d'exception
      levée, code HTTP retourné. Vérifier contre un faux test.
- [ ] Shape exacte du `inputSchema` renvoyé par le MCP Pappers :
      confirmer qu'elle est directement compatible avec le champ
      `input_schema` des tools Anthropic, ou documenter l'adaptation
      minimale (renommage `inputSchema` → `input_schema`, éventuel
      strip de `$schema` / `additionalProperties`).

### Commit phase 1

`story(S02): refine — MCP Python SDK version, tool discovery pattern`

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
"""Client MCP Pappers en transport streamable-http."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import structlog

from genial_agent.config import settings

logger = structlog.get_logger(__name__)

PAPPERS_BASE_URL = "https://mcp.pappers.fr"
DEFAULT_TIMEOUT_S = 30

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
    name: str
    description: str
    input_schema: dict[str, Any]


def _build_url() -> str:
    """Construit l'URL complète côté serveur. Ne jamais logguer.

    La clé API est dans le path URL (cf. pappers-mcp.md §2).
    """
    if not settings.PAPPERS_API_KEY:
        raise RuntimeError("PAPPERS_API_KEY not set")
    return f"{PAPPERS_BASE_URL}/{settings.PAPPERS_API_KEY}"


async def list_available_tools() -> list[PappersTool]:
    """Discovery des tools exposés par le MCP Pappers."""
    # Implémentation à compléter en phase 1 elicitation avec le SDK
    # MCP Python retenu. Principe général :
    # 1. Ouvrir un client streamable-http sur _build_url().
    # 2. Appeler tools/list.
    # 3. Parser en PappersTool.
    # 4. Logguer la liste sans l'URL.
    raise NotImplementedError


def to_anthropic_schema(tools: list[PappersTool]) -> list[dict[str, Any]]:
    """Convertit des PappersTool vers le format tools attendu par l'API
    Anthropic Messages (`name`, `description`, `input_schema`).

    Consommé par S03 au moment de construire l'appel messages.create().
    """
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


async def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Exécute un tool Pappers, avec cache 24 h + retry tenacity.

    Ordre :
    1. Cache hit → retourne immédiatement, 0 crédit consommé.
    2. Si mode dégradé activé (crédits épuisés, cf. S07) → ne consulte
       que le cache ; si miss, lève `CreditsExhausted`.
    3. Sinon, retry tenacity (3 tentatives, backoff expo 0.5→1→2 s
       + jitter) ; pas de retry sur 401/403/404.
    4. Stocke dans le cache et retourne.
    """
    # Implémentation détaillée en phase 2 dev — voir mcp_cache.py.
    raise NotImplementedError


async def prewarm_cache() -> None:
    """Préchauffe le cache sur les 3 entités officielles (LVMH, BNP, Carrefour)
    pour que le mode dégradé fonctionne même en sortie de boot (cahier §5.4).

    À appeler une fois au démarrage (cl.on_chat_start ou setup).
    """
    # Appels : search_company("LVMH") / ("BNP Paribas") / ("Carrefour")
    # puis get_company / get_directors / get_financials sur les SIREN
    # obtenus. Les miss sont tolérés.
    raise NotImplementedError


async def healthcheck() -> dict[str, Any]:
    """Vérifie la connectivité Pappers sans consommer de crédit.

    Returns:
        {"status": "ok" | "ko", "latency_ms": int, "tools_count": int}
    """
    import time
    start = time.monotonic()
    try:
        tools = await asyncio.wait_for(
            list_available_tools(), timeout=DEFAULT_TIMEOUT_S
        )
        latency = int((time.monotonic() - start) * 1000)
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
- Méthode MCP standard : `tools/list`.

### Gotchas documentés

- **Ne jamais logguer `_build_url()` directement** → fuite de clé.
- Transport SSE **interdit** par Pappers (erreur silencieuse possible).
- Timeout long raisonnable (30 s) car le premier handshake peut être lent.
- Si l'API MCP Pappers renvoie 404 : probablement clé invalide (ils ne
  valident pas séparément, le chemin complet fait office d'auth).
- **Retry tenacity** : ne pas retry sur 401/403 (auth) ni 404 (entité
  inconnue) — ça spamme sans résoudre. Retry uniquement 5xx / 429 /
  timeouts réseau.
- **Canonicalisation des args du cache** : utiliser `json.dumps(args,
  sort_keys=True)` sinon `{"a":1,"b":2}` et `{"b":2,"a":1}` donnent
  des hash différents et manquent le cache.
- **Mapping input_schema** : le MCP renvoie souvent la clé camelCase
  `inputSchema` (spec MCP). Anthropic attend `input_schema`. Faire le
  renommage dans `to_anthropic_schema`.

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S02_mcp_client.py
import pytest
from unittest.mock import patch

from genial_agent import mcp_pappers
from genial_agent.config import settings


def test_build_url_uses_env_key(monkeypatch):
    monkeypatch.setattr(settings, "PAPPERS_API_KEY", "abc123")
    url = mcp_pappers._build_url()
    assert url == "https://mcp.pappers.fr/abc123"


def test_build_url_fails_if_no_key(monkeypatch):
    monkeypatch.setattr(settings, "PAPPERS_API_KEY", "")
    with pytest.raises(RuntimeError, match="PAPPERS_API_KEY"):
        mcp_pappers._build_url()


def test_retained_tools_is_set():
    assert isinstance(mcp_pappers.RETAINED_TOOLS, set)
    assert len(mcp_pappers.RETAINED_TOOLS) > 0


def test_log_does_not_contain_api_key(caplog):
    # Vérifie qu'aucun log ne contient la clé, même via _build_url
    # (à étendre avec un vrai appel mocké)
    ...
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
from genial_agent.mcp_pappers import PappersTool, to_anthropic_schema


def test_mapping_minimal_shape():
    tools = [
        PappersTool(
            name="get_company",
            description="Get company by SIREN",
            input_schema={"type": "object", "properties": {"siren": {"type": "string"}}},
        )
    ]
    out = to_anthropic_schema(tools)
    assert out == [
        {
            "name": "get_company",
            "description": "Get company by SIREN",
            "input_schema": {"type": "object", "properties": {"siren": {"type": "string"}}},
        }
    ]
```

#### Intégration (réel, skip si pas de clé)

```python
# tests/integration/test_S02_pappers_live.py
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
async def test_tools_discovery_contains_company_tool():
    tools = await mcp_pappers.list_available_tools()
    names = {t.name for t in tools}
    # Au moins un tool company-related doit exister
    assert any("company" in n.lower() or "entreprise" in n.lower() for n in names)
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

- [ ] Aucun `print(url)` ou équivalent qui leak la clé.
- [ ] Vérifier qu'un `grep -r "PAPPERS_API_KEY" src/` ne retourne que
      `config.py` et `mcp_pappers.py`.
- [ ] `healthcheck()` gère au moins : timeout, erreur réseau, auth failed.
- [ ] Transport forcé à `streamable-http`, jamais `sse` ni `stdio`.
- [ ] Tests d'intégration skip proprement si clé absente (pas d'erreur
      rouge, juste un skip visible).
- [ ] `list_available_tools()` logue la liste des noms mais **pas l'URL**.
- [ ] Cache : test `test_cache_args_canonical_order` vert.
- [ ] Retry tenacity : vérifier en mockant qu'un 401 lève immédiatement
      sans 3 tentatives.
- [ ] `to_anthropic_schema` : shape du dict retourné correspond à
      l'attendu de `anthropic.messages.create(tools=[...])`.
- [ ] `prewarm_cache` implémenté et couvre les 3 SIREN officiels.

### Commit phase 3

`review(S02): approved`

---

## ✅ Critères d'acceptation

- [ ] `healthcheck()` retourne `{"status": "ok", ...}` avec une vraie clé.
- [ ] Le nombre de tools remontés par Pappers est loggé au premier run.
- [ ] Tous les tests unitaires passent sans clé (cache, mapping).
- [ ] Les tests d'intégration passent avec une vraie clé.
- [ ] Deux appels identiques `call_tool("get_company", {"siren": "..."})`
      rapprochés consomment **1 crédit** (le second est cache hit).
- [ ] `to_anthropic_schema` retourne un payload directement utilisable
      par `anthropic.messages.create(tools=...)`.
- [ ] `gitleaks detect` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S02 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
