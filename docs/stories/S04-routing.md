# S04 — Routing Haiku ↔ Sonnet

> **Statut** : ⬜ à faire
> **Durée estimée** : 1 h
> **Parallélisable avec** : —

---

## 📍 Contexte

Option B' (agent-first) : pré-routeur keyword côté code (0 LLM), Haiku
comme agent par défaut avec tool `escalate_to_sonnet`, cap dur backend
en filet ultime.

Sources de vérité :
- `docs/cahier-des-charges.md` §5.3 (routing détaillé), §6.1 (modèles).
- Story S03 (boucle agent).

---

## 🔒 Prérequis

- [ ] S01 → S03 terminées.

## 🔑 Inputs utilisateur requis

- Aucun nouveau (réutilise `ANTHROPIC_API_KEY`).

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/routing.py` :
  - Pré-routeur keyword → tier initial.
  - Tool `escalate_to_sonnet(reason)` injecté dans les tools exposés à
    Haiku.
  - Boucle de détection d'escalade et re-lancement de `run_turn` avec
    Sonnet en préservant le contexte.
  - Cap backend : max 5 tool calls, max 15 s wall-clock.
  - Metadata retournée indiquant quel modèle a répondu (pour badge UI).

### Hors scope

- UI Chainlit consommant ces metadata (S06).
- Observabilité détaillée (S07).

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Vérifier que les patterns regex de détection d'intent en français
      couvrent bien les cas typiques sans faux positifs (ex: "compare"
      vs "comparable"). Liste initiale :
      - `\b(compare|comparaison|versus|vs\.?)\b`
      - `\b(dossier complet|due diligence|due dil)\b`
      - `\b(évolution|sur \d+ ans?|3 ans?)\b`
      - `\b(lequel|laquelle)\b`
      - `\b(similaires? à|concurrents de)\b`
      - `(SIREN[^\w]*\d{9}.*SIREN[^\w]*\d{9})` (deux SIREN = multi-entity)
- [ ] Vérifier : est-ce qu'un outil "virtuel" (escalate_to_sonnet) qui
      n'est pas exposé par MCP mais ajouté localement à la liste de tools
      de l'Anthropic SDK fonctionne sans souci. Réponse attendue : oui
      (le SDK ne distingue pas tools MCP vs tools locaux).
- [ ] Best practice pour un cap wall-clock en asyncio : `asyncio.wait_for`
      autour de la boucle agent, avec gestion propre du timeout (yield
      d'un event "aborted" et append au state).

### Points à résoudre

- [ ] Comportement à la bascule : quand Haiku appelle `escalate_to_sonnet`,
      Sonnet doit voir tout l'historique **y compris les tool calls et
      tool results déjà effectués**. Confirmer que ça fonctionne en
      repassant simplement le même `state.messages`.

### Commit phase 1

`story(S04): refine — keyword regex set, escalate tool schema, timeout pattern`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/routing.py`
- Modification `src/genial_agent/agent.py` pour accepter les tools
  locaux additionnels (escalate).
- `tests/unit/test_S04_routing.py`
- `tests/integration/test_S04_routing_live.py`

### `routing.py` — squelette

```python
"""Routing Haiku → Sonnet : pré-routeur keyword + escalate tool + cap."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog

from genial_agent.agent import ConversationState, run_turn
from genial_agent.models import ModelTier

logger = structlog.get_logger(__name__)

# Ordre important : premier match gagne
COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(compare|comparaison|versus|vs\.?)\b", re.IGNORECASE),
    re.compile(r"\b(dossier\s+complet|due\s+diligence|due\s+dil)\b", re.IGNORECASE),
    re.compile(r"\b(évolution|sur\s+\d+\s+ans?)\b", re.IGNORECASE),
    re.compile(r"\b(lequel|laquelle|lesquels?)\b", re.IGNORECASE),
    re.compile(r"\b(similaires?\s+à|concurrents?\s+de)\b", re.IGNORECASE),
]

# Heuristique multi-SIREN : 2 SIREN détectés = complexe
SIREN_RE = re.compile(r"\b\d{9}\b")

MAX_TOOL_CALLS = 5
WALL_CLOCK_S = 15


ESCALATE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "escalate_to_sonnet",
    "description": (
        "Appelle cet outil SI ET SEULEMENT SI la requête nécessite un "
        "raisonnement profond que tu ne peux pas produire seul : "
        "comparaison multi-entités, analyse financière fine, enchaînement "
        "de plus de 3 tool calls supplémentaires, cross-referencing. "
        "L'appel va te remplacer par Sonnet qui reprendra avec tout le contexte."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Explication courte de pourquoi tu escalades.",
            }
        },
        "required": ["reason"],
    },
}


@dataclass
class RoutingResult:
    model_used: ModelTier
    escalated: bool
    escalation_reason: str | None
    capped: bool
    tool_calls_count: int


def pick_initial_tier(user_message: str) -> ModelTier:
    """Pré-routeur keyword. Retourne Sonnet si complexe, sinon Haiku."""
    if any(p.search(user_message) for p in COMPLEX_PATTERNS):
        return ModelTier.SONNET
    if len(SIREN_RE.findall(user_message)) >= 2:
        return ModelTier.SONNET
    return ModelTier.HAIKU


async def run_routed_turn(
    state: ConversationState,
    user_message: str,
) -> AsyncIterator[dict[str, Any]]:
    """Exécute un tour avec routing + escalade + cap."""
    initial_tier = pick_initial_tier(user_message)
    logger.info("routing_initial", tier=initial_tier.value, reason="keyword_or_default")

    # Tool escalate disponible uniquement en tier Haiku
    extra_tools = [ESCALATE_TOOL_SCHEMA] if initial_tier == ModelTier.HAIKU else []

    try:
        async with asyncio.timeout(WALL_CLOCK_S):
            async for event in run_turn(state, user_message, tier=initial_tier, extra_tools=extra_tools):
                # Détection d'un tool call escalate
                if (
                    event.get("type") == "tool_use"
                    and event.get("name") == "escalate_to_sonnet"
                ):
                    reason = event.get("input", {}).get("reason", "unknown")
                    logger.info("routing_escalate", reason=reason)
                    yield {"type": "escalation", "reason": reason}
                    # Reboucler avec Sonnet, mêmes messages
                    async for ev2 in run_turn(state, "", tier=ModelTier.SONNET, continuation=True):
                        yield ev2
                    yield {"type": "routing_done", "model_used": "sonnet", "escalated": True}
                    return
                # Cap tool calls
                if state.tool_calls_count >= MAX_TOOL_CALLS:
                    logger.warning("routing_tool_cap_hit", count=state.tool_calls_count)
                    yield {"type": "capped", "reason": "tool_calls_max"}
                    return
                yield event
            yield {"type": "routing_done", "model_used": initial_tier.value, "escalated": False}
    except TimeoutError:
        logger.warning("routing_timeout", seconds=WALL_CLOCK_S)
        yield {"type": "capped", "reason": "wall_clock"}
```

### Modification `agent.py`

- Ajouter un paramètre `extra_tools: list[dict] | None = None` à
  `run_turn` pour injecter le schema `escalate_to_sonnet`.
- Ajouter un paramètre `continuation: bool = False` qui, si True, ne
  réappend pas un user message (on continue la conversation).

### Gotchas documentés

- L'escalade doit préserver **tous** les tool results déjà obtenus dans
  `state.messages`. Ne pas réinitialiser l'historique.
- Le timeout wall-clock peut couper au milieu d'un stream de texte →
  vérifier que l'UI consomme proprement le `{"type": "capped"}`.
- `asyncio.timeout` requiert Python 3.11+.

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S04_routing.py
import pytest
from genial_agent.routing import pick_initial_tier
from genial_agent.models import ModelTier


@pytest.mark.parametrize(
    "msg,expected",
    [
        ("Donne-moi la fiche de LVMH", ModelTier.HAIKU),
        ("Compare Carrefour et Casino", ModelTier.SONNET),
        ("Fais-moi un dossier complet sur Total", ModelTier.SONNET),
        ("Évolution du CA sur 3 ans", ModelTier.SONNET),
        ("Les dirigeants de BNP", ModelTier.HAIKU),
        ("Similaires à Michelin", ModelTier.SONNET),
        ("Lequel est le plus rentable ?", ModelTier.SONNET),
        # Multi-SIREN
        ("Compare 123456789 et 987654321", ModelTier.SONNET),
    ],
)
def test_pick_initial_tier(msg, expected):
    assert pick_initial_tier(msg) == expected


def test_escalate_tool_schema_shape():
    from genial_agent.routing import ESCALATE_TOOL_SCHEMA
    assert ESCALATE_TOOL_SCHEMA["name"] == "escalate_to_sonnet"
    assert "reason" in ESCALATE_TOOL_SCHEMA["input_schema"]["properties"]
```

#### Intégration

```python
# tests/integration/test_S04_routing_live.py
import os
import pytest
from genial_agent.routing import run_routed_turn
from genial_agent.agent import ConversationState

pytestmark = pytest.mark.integration
SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))


@pytest.mark.skipif(SKIP, reason="keys missing")
async def test_simple_stays_haiku():
    state = ConversationState()
    events = [e async for e in run_routed_turn(state, "Fiche LVMH")]
    done = [e for e in events if e.get("type") == "routing_done"][-1]
    assert done["model_used"] == "haiku"
    assert done["escalated"] is False


@pytest.mark.skipif(SKIP, reason="keys missing")
async def test_complex_goes_sonnet():
    state = ConversationState()
    events = [e async for e in run_routed_turn(state, "Compare LVMH et Kering sur 3 ans")]
    done = [e for e in events if e.get("type") == "routing_done"][-1]
    assert done["model_used"] == "sonnet"


@pytest.mark.skipif(SKIP, reason="keys missing")
async def test_cap_on_runaway(monkeypatch):
    # Force un cap artificiel en abaissant le seuil
    import genial_agent.routing as r
    monkeypatch.setattr(r, "MAX_TOOL_CALLS", 1)
    state = ConversationState()
    events = [e async for e in run_routed_turn(state, "Compare 5 entreprises du CAC40")]
    assert any(e.get("type") == "capped" for e in events)
```

### Commit phase 2

`feat(S04): keyword router + escalate tool + wall-clock cap`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] Les regex sont bien insensibles à la casse et robustes aux accents
      (test avec `Évolution` et `évolution`).
- [ ] `run_routed_turn` est un async generator propre, pas de deadlock.
- [ ] L'escalade transmet le state intact à Sonnet (vérifier sur un
      test log).
- [ ] Le timeout est bien `15 s` et cette constante n'est pas dupliquée.
- [ ] Pas de log qui contient le message utilisateur entier (PII
      potentiel).

### Commit phase 3

`review(S04): approved`

---

## ✅ Critères d'acceptation

- [ ] Tous les tests paramétrés de `pick_initial_tier` passent.
- [ ] Test `test_simple_stays_haiku` passe live.
- [ ] Test `test_complex_goes_sonnet` passe live.
- [ ] Test `test_cap_on_runaway` passe (cap artificiel).
- [ ] `gitleaks` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S04 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
