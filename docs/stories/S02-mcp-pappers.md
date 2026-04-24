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
- [ ] `.env` local contient `PAPPERS_API_KEY=<valeur réelle>`.

## 🔑 Inputs utilisateur requis

- [ ] `PAPPERS_API_KEY` générée sur pappers.fr (Mon compte → Mon API).
- [ ] Solde crédits Pappers ≥ 50 (pour tests intégration weekend).

---

## 🎯 Scope

### Dans le scope

- Module `src/genial_agent/mcp_pappers.py` exposant :
  - Construction d'URL côté serveur (clé jamais exposée).
  - Connexion via transport `streamable-http`.
  - Discovery des tools exposés (`list_tools`).
  - Filtrage applicatif des tools retenus.
  - Fonction `healthcheck()` non destructive.
- Gestion d'erreur réseau, timeout, auth.
- Logging structuré (sans URL complète).

### Hors scope

- Utilisation par l'agent (S03).
- Cache applicatif (S07).
- Retry/backoff avancé (S07).

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
- [ ] Confirmer le format de discovery : méthode JSON-RPC `tools/list`,
      shape du résultat (liste de `{name, description, inputSchema}`).
- [ ] Identifier les tools Pappers typiques (noms probables : `search_company`,
      `get_company`, `get_directors`, `get_financials`, `search_directors`...) —
      la liste exacte sera obtenue au premier discovery, mais documenter
      ceux qu'on retiendra.

### Points à résoudre

- [ ] Le client MCP Python a-t-il un timeout natif ? Sinon, imposer un
      timeout par appel via `asyncio.wait_for` (30 s par défaut).
- [ ] Comportement en cas d'échec d'auth (clé invalide) : type d'exception
      levée, code HTTP retourné. Vérifier contre un faux test.

### Commit phase 1

`story(S02): refine — MCP Python SDK version, tool discovery pattern`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/mcp_pappers.py` — client.
- `src/genial_agent/config.py` — chargement `.env` centralisé (si non
  déjà fait par S01 phase 1).
- `tests/unit/test_S02_mcp_client.py`
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
# réduire les tokens et le coût en crédits). Liste à ajuster après
# le premier run de discovery.
RETAINED_TOOLS: set[str] = {
    # À confirmer en phase 1 elicitation + premier run
    "search_company",
    "get_company",
    "get_directors",
    "get_financials",
}


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

`feat(S02): Pappers MCP client with streamable-http transport`

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

### Commit phase 3

`review(S02): approved`

---

## ✅ Critères d'acceptation

- [ ] `healthcheck()` retourne `{"status": "ok", ...}` avec une vraie clé.
- [ ] Le nombre de tools remontés par Pappers est loggé au premier run.
- [ ] Tous les tests unitaires passent sans clé.
- [ ] Les tests d'intégration passent avec une vraie clé.
- [ ] `gitleaks detect` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S02 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
