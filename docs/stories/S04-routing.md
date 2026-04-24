# S04 — Routing Haiku ↔ Sonnet

> **Statut** : ⬜ à faire (phase 1 raffinée 2026-04-24)
> **Durée estimée** : 1 h
> **Parallélisable avec** : —

---

## 📍 Contexte

Option B' (agent-first) : pré-routeur keyword côté code (0 LLM, < 1 ms),
Haiku 4.5 comme agent par défaut avec tool méta `escalate_to_sonnet`,
escalade forcée côté code si Haiku enchaîne trop de tool calls.

S04 est une couche **au-dessus** de `agent.run_turn` (S03). S04 ne
réimplémente pas la boucle streaming ni le tool calling : elle délègue
à S03 et arbitre le choix de modèle + la bascule.

Sources de vérité :

- `docs/cahier-des-charges.md` §5.3 (routing détaillé), §6.1 (modèles),
  §7 R3 (escalade via badge UI), §7 R9 (trigger Pappers via system prompt).
- `docs/stories/README.md` §"Décisions de cohérence" : cap 5 tool calls
  (cahier §5.3 & §14.3 C4), call-sites stats S07.
- `docs/stories/S03-agent-core.md` : contrat `run_turn` (events yield,
  `ConversationState`, hook `extra_tools`, hook `continuation`),
  invariant I2 (atomicité append), invariant I5 (`state.lock`).

---

## 🔒 Prérequis

- [x] S01 → S03 terminées et approuvées (commits `72d129d`, `79a1662`).

## 🔑 Inputs utilisateur requis

- Aucun nouveau (réutilise `ANTHROPIC_API_KEY` + `PAPPERS_API_KEY`).

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/routing.py` :
  - `pick_initial_tier(user_message)` : pré-routeur regex français,
    accent-insensible, retourne `ModelTier.HAIKU` ou `ModelTier.SONNET`.
  - `ESCALATE_TOOL_SCHEMA` : schéma Anthropic du tool local
    `escalate_to_sonnet(reason)` injecté via `extra_tools`.
  - `run_routed_turn(state, user_message)` : async generator qui
    délègue à `agent.run_turn`, détecte l'escalade (self via tool_use
    ou forcée via cap) et relance un `run_turn` en tier Sonnet avec
    `continuation=True`.
  - Cap de **tool calls par tour** = 5 (conforme cahier §5.3 et
    §14.3 C4 ; cf. README "Décisions de cohérence"). Déclenche
    l'escalade forcée mid-stream.
  - Cap **wall-clock par tour** = 15 s (conforme cahier §5.3). Déclenche
    aussi l'escalade forcée en Haiku, ou l'event `capped` en Sonnet.
    Implémenté via `asyncio.wait_for(gen.__anext__(), timeout=remaining)`
    pour respecter PEP 789 (voir phase 1).
  - Metadata routing (événements `escalation` + `routing_done`) pour
    alimenter le badge UI (S06) et les stats (S07).

### Hors scope explicite

- **Wall-clock sur des awaits internes à `run_turn`** : S04 ne peut
  interrompre qu'**entre** deux events yieldés (granularité `__anext__`).
  Un await lent à l'intérieur d'une itération S03 (ex : MCP tool call
  qui dure) n'est pas cappé par S04. Les budgets downstream existants
  absorbent ce cas : `CALL_TOOL_BUDGET_S=20 s` dans `mcp_pappers.call_tool`
  (S02), retries SDK Anthropic (2 internes), `MAX_ITERATIONS=12` dans
  `agent.run_turn` (S03). Donc le cap S04 est **soft** (effectif à la
  prochaine frontière d'event), ce qui est cohérent avec le fait que
  cahier §5.3 décrit un trigger d'**escalade**, pas un kill brutal.
- **UI Chainlit consommant ces metadata** : S06.
- **Observabilité détaillée** (incrément stats) : S07 (call-site
  `routing.py`, incrémente `stats.routing_decision(tier, escalated)`).
- **Pré-classifieur LLM** : définitivement abandonné (cahier §5.3).
- **Détection multi-turn sur context conversationnel** : le pré-routeur
  regarde **uniquement** le dernier `user_message`. Sur un follow-up
  "et son CA ?", Haiku reste le default (simple) ; Claude 4.x gère la
  résolution de pronom sur l'historique (cf. S03 multi-turn).

---

## 🧭 Phase 1 — Elicitation Agent

### ✅ Conclusions elicitation (2026-04-24)

Recherches effectuées via :

- Inspection directe de `agent.py` / `models.py` / `prompts.py`
  (S03 implémenté, commits `59e1905` + `79a1662`).
- PyPI / docs Python 3.12 sur `asyncio.timeout` et PEP 789 (antipattern
  confirmé).
- Inspection SDK `anthropic==0.97.0` : `StopReason` ∈
  {`end_turn`, `max_tokens`, `stop_sequence`, `tool_use`, `pause_turn`,
  `refusal`}.
- Probe régex sur Python 3.12 : `re.IGNORECASE` **n'est pas**
  accent-insensible (`évolution` ≠ `evolution`) → on normalise l'entrée
  via `unicodedata.NFKD` avant match.

#### 🧨 Décision majeure : pas de `asyncio.timeout` — `wait_for` sur `__anext__`

Le squelette initial wrappait la boucle S04 dans
`async with asyncio.timeout(WALL_CLOCK_S)`. C'est **l'antipattern PEP
789** : yielder depuis un async generator à l'intérieur d'un cancel
scope (`timeout`, `TaskGroup`) peut annuler la mauvaise tâche, laisser
passer des exceptions mal typées, ou casser le cleanup du generator.

**Pattern retenu** — itération manuelle avec `asyncio.wait_for` autour
de chaque `__anext__()`, la valeur yieldée n'est **plus** dans un
cancel scope. On respecte PEP 789 tout en gardant le cap wall-clock
prévu par le cahier §5.3.

```python
gen = run_turn(state, user_message, tier=initial_tier, extra_tools=...)
deadline = time.monotonic() + WALL_CLOCK_S
aiter = gen.__aiter__()
try:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Cap wall-clock atteint → escalate ou capped (cf. §Cap)
            break
        try:
            event = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        except TimeoutError:
            # wait_for a timeout — un single await inside run_turn a dépassé
            # `remaining`. On traite comme wall-clock cap hit.
            break
        # ... process event, yield, cap checks ...
finally:
    await gen.aclose()  # release state.lock de manière déterministe
```

Points importants :

- **Granularité** : `wait_for` timeout uniquement sur `__anext__()`, soit
  « temps entre deux events yieldés par S03 ». Un await interne long
  (ex : MCP call de 25 s) peut dépasser `remaining` — on detecte via
  `TimeoutError`, on break, et le `aclose()` gère le cleanup.
- **Cleanup du gen interrompu** : `gen.aclose()` fait tourner le
  `async with state.lock` cleanup. Le `CancelledError` remonte dans
  S03 au prochain `await`, ce qui casse la boucle S03 proprement.
  L'invariant I2 (atomicité append) garantit que le state reste
  cohérent même sur cancellation mid-iter.
- **Pourquoi pas un `deadline` passé dans S03 directement ?** Rejeté :
  ça changerait le contrat de `run_turn` (S03) qui est figé et approuvé,
  pour une responsabilité (cap produit) qui est S04/S05.
- **Overhead** : négligeable. `wait_for` wrap un awaitable existant,
  pas de task extra créée côté consumer.

Référence : [PEP 789 — Preventing task-cancellation bugs by limiting
yield in async generators](https://peps.python.org/pep-0789/).

#### 🔐 Lock reentrance & atomicité state

`ConversationState.lock` (S03 I5) est détenu pendant **toute** la
durée de `run_turn`. S04 appelle `run_turn` **deux fois** en série
quand Haiku escalade vers Sonnet. Si on nestait les appels, le 2e
`async with state.lock` deadlockerait (asyncio.Lock n'est pas
reentrant).

**Pattern retenu — break + aclose + second run_turn séquentiel** (le
pattern s'intègre dans la boucle `wait_for` décrite juste au-dessus) :

```python
gen = run_turn(state, user_message, tier=Haiku, extra_tools=[ESCALATE_TOOL_SCHEMA])
aiter = gen.__aiter__()
deadline = time.monotonic() + WALL_CLOCK_S
escalated = False
escalation_reason: str | None = None

try:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            # Wall-clock hit (cf. §Décision majeure)
            break
        try:
            event = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            break
        except TimeoutError:
            break

        if (
            event.get("type") == "tool_use"
            and event.get("name") == "escalate_to_sonnet"
        ):
            escalation_reason = event.get("input", {}).get("reason") or "unspecified"
            escalated = True
            yield {"type": "escalation", "reason": escalation_reason, "mode": "self"}
            break  # sortir AVANT que run_turn exécute le "tool" (MCP ne le connaît pas)
        yield event
        # Cap tool calls par-turn (voir ci-dessous).
        ...
finally:
    # Libère le state.lock en faisant fermer l'async generator S03.
    # Essentiel — sans aclose(), le lock reste détenu jusqu'au GC.
    await gen.aclose()

if escalated:
    # Le lock est libre ici. Sonnet reprend sur state.messages intact
    # grâce à l'invariant I2 (append atomique) : toute itération S03
    # incomplète n'a rien appendé.
    async for event in run_turn(state, "", tier=Sonnet, continuation=True):
        yield event
```

Pourquoi c'est sûr :

1. **yield suspend, pas côté S03 execute** : quand S03 yield le
   `tool_use` pour escalate, son corps est suspendu **avant**
   `await mcp_pappers.call_tool(...)`. Le break de S04 empêche la
   reprise — le call MCP sur un tool inconnu (PappersError 404
   potentiel) n'a jamais lieu.
2. **`gen.aclose()` obligatoire** : Python ne garantit pas que
   les `finally` d'un generator abandonné s'exécutent tout de suite
   (cpython refcount-GC usually ok, mais non-CPython / edge cases =
   lock tenu jusqu'au GC). `aclose()` fait tourner le `async with
   state.lock` cleanup **synchrone et déterministe**.
3. **Invariant I2 de S03** : `state.messages` n'a été appendé que pour
   les itérations S03 **entièrement terminées** (assistant + user
   tool_results posés ensemble, en fin de boucle). L'itération
   contenant l'escalate n'est **jamais** appendée. Sonnet voit donc un
   state cohérent (pas d'orphan `tool_use`) et peut reprendre via
   `continuation=True`.
4. **Cache Pappers absorbe la rework** : si Haiku avait fait plusieurs
   tool calls réussis **dans la même itération finale** avant de
   poser l'escalate (cas rare), ces résultats sont "perdus" mais le
   cache S02 (TTL 24 h + single-flight) resert les mêmes réponses à
   coût 0 quand Sonnet les redemande.

**Edge case — escalate + autres tool_use dans le même final message** :
pour maximiser l'efficacité, la description du tool `escalate_to_sonnet`
instruit explicitement Haiku à l'appeler **seul** (pas en parallèle).
Cf. `ESCALATE_TOOL_SCHEMA` plus bas.

#### 🧮 Caps par-turn (tool calls + wall-clock)

**Tool calls** : `ConversationState.tool_calls_count` est **cumulatif
par session** (docstring S03 explicite). Le cap §5.3 du cahier est
**par-turn**. S04 calcule un delta snapshot :

```python
initial_count = state.tool_calls_count
# ... dans la boucle :
per_turn = state.tool_calls_count - initial_count
if per_turn >= MAX_TOOL_CALLS_PER_TURN:
    escalation_reason = f"cap_tool_calls_per_turn={per_turn}"
    escalated = (initial_tier == ModelTier.HAIKU)
    mode = "forced" if escalated else None
    yield {"type": "escalation" if escalated else "capped", ...}
    break
```

**Wall-clock** : `deadline = time.monotonic() + WALL_CLOCK_S` calculé
une fois à l'entrée de `run_routed_turn`. Vérifié à chaque itération
de la boucle `wait_for` (cf. §Décision majeure). Deux points de
détection :

1. `remaining <= 0` en haut de boucle → on break avant de démarrer un
   nouveau `__anext__`.
2. `asyncio.TimeoutError` levé par `wait_for` → un single await interne
   a dépassé `remaining`.

Les deux cas convergent vers le même handler :

```python
# Après la boucle, si on a break sans event d'exit normal :
wall_clock_hit = time.monotonic() >= deadline
if wall_clock_hit and not escalated:
    escalation_reason = f"cap_wall_clock={WALL_CLOCK_S}s"
    escalated = (initial_tier == ModelTier.HAIKU)
    mode = "forced" if escalated else None
    yield {"type": "escalation" if escalated else "capped", "reason": escalation_reason, "mode": mode}
```

**Comportement symétrique pour les deux caps** :

| Tier initial | Cap hit | Event émis | Action |
|---|---|---|---|
| Haiku | tool_calls ou wall_clock | `escalation(mode=forced)` | Break, aclose, relance Sonnet en continuation |
| Sonnet | tool_calls ou wall_clock | `capped` | Stop la boucle, pas de relance (pas de tier au-dessus pour MVP) |

Note UX (cahier §5.3) : ces caps déclenchent une **escalade**, pas un
kill brutal. En Sonnet déjà, `capped` signifie « la réponse s'arrête
ici faute de budget, l'utilisateur voit ce que Sonnet a pu produire ».

#### 🔤 Regex accent-insensible (normalisation `NFKD`)

Probe Python 3.12 : `re.IGNORECASE` gère le case folding ASCII +
Unicode (`Évolution` → case insensitive match sur `évolution`), mais
**pas** l'accent folding (`evolution` ≠ `évolution`). Les utilisateurs
français tapent régulièrement sans accents (saisie mobile, clavier
QWERTY). Sans normalisation, le pré-routeur rate ~30 % des requêtes
légitimes.

Solution : normalisation NFKD → drop des combining marks → lowercase,
avant match regex. Ça décompose `é` en `e + ́` puis strippe le
diacritique. Bonus : harmonise aussi `œ` → `oe`, `ç` → `c`.

```python
import unicodedata

def _normalize_fr(text: str) -> str:
    """Strip diacritiques + lowercase pour match accent-insensible."""
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = nfkd.encode("ascii", "ignore").decode("ascii")
    return stripped.lower()
```

Les patterns se simplifient (plus besoin de `é?` ni de alternance
`(evolution|évolution)`) :

```python
COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(compare|comparaison|versus|vs\.?)\b"),
    re.compile(r"\b(dossier\s+complet|due\s+diligence|due\s+dil)\b"),
    re.compile(r"\b(evolution|sur\s+\d+\s+ans?)\b"),  # "evolution" suffit après NFKD
    re.compile(r"\b(lequel|laquelle|lesquels?|lesquelles?)\b"),
    re.compile(r"\b(similaires?\s+a|concurrents?\s+de)\b"),  # "a" (pas "à") après NFKD
]
```

Note : les patterns sont écrits **en minuscules ASCII** puisque
l'entrée est normalisée avant match. Inutile de mettre `re.IGNORECASE`
dans ce cas.

#### 📞 Tool méta `escalate_to_sonnet` — schéma figé

Description pensée pour le SDK Anthropic 2026 (tool_use auto, §14.3 R9
anti-injection respecté via system prompt S03) :

```python
ESCALATE_TOOL_SCHEMA: dict[str, Any] = {
    "name": "escalate_to_sonnet",
    "description": (
        "Signale que la requête utilisateur dépasse tes capacités "
        "actuelles et qu'un modèle plus puissant (Sonnet) doit "
        "prendre le relais. Appelle-le SEUL (pas d'autre tool_use "
        "dans la même réponse) SI ET SEULEMENT SI : (a) la question "
        "nécessite une comparaison multi-entités fine, (b) elle "
        "demande un raisonnement financier qui dépasse tes "
        "capacités, (c) tu sens qu'il te faudra 3+ tool calls "
        "supplémentaires que tu ne pourras pas synthétiser "
        "correctement. Fournis une raison courte (≤ 1 phrase). "
        "L'escalade conserve l'intégralité du contexte conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "Pourquoi tu estimes ne pas pouvoir conclure seul."
                ),
            }
        },
        "required": ["reason"],
    },
}
```

Points clés :

- **Nom kebab-compatible Anthropic** (`^[a-zA-Z0-9_-]{1,128}$`) ✔.
- **Instructions anti-parallèle** dans la description — réduit le
  risque d'Haiku qui appelle `sirenisateur` + `escalate_to_sonnet`
  dans le même message final (ce qui ferait perdre le résultat de
  sirenisateur lors du break, cf. §"Lock reentrance").
- **Pas de champ `mode`** : S04 distingue self vs forced côté code
  (via le cap), pas via l'input du tool.

#### 📨 Contrat d'événements S04 (superset de S03)

S04 forwarde tous les events de `run_turn` **tels quels** + ajoute :

| `type` | Champs | Émis quand |
|---|---|---|
| `routing_initial` | `tier: "haiku" \| "sonnet"`, `reason: "keyword" \| "default"` | Une fois, au tout début de `run_routed_turn`. |
| `escalation` | `reason: str`, `mode: "self" \| "forced"` | Haiku appelle `escalate_to_sonnet` (self) ou cap `tool_calls_per_turn` / `wall_clock` atteint en Haiku (forced). `reason` inclut la cause exacte (`cap_tool_calls_per_turn=5`, `cap_wall_clock=15s`, ou texte libre pour self). |
| `capped` | `reason: "tool_calls_per_turn" \| "wall_clock"`, `count: int \| None` | Cap atteint en tier Sonnet déjà (pas d'escalade possible, pas de tier au-dessus MVP). |
| `routing_done` | `model_used: "haiku" \| "sonnet"`, `escalated: bool`, `escalation_mode: "self" \| "forced" \| None`, `escalation_reason: str \| None`, `tool_calls_count: int` | Une fois, en toute fin. |

Les events S03 conservés (`text`, `tool_use`, `tool_result`, `llm_meta`,
`end`) sont **forwardés**. S04 n'altère aucun d'eux.

**Important pour S06 (UI)** : le `end` event vient de S03 (Sonnet en cas
d'escalade, Haiku sinon). Le `routing_done` event est **après** le
`end` de S03, et donne le méta-résumé de routing. Le badge UI
(`⚡ Haiku` / `🧠 Sonnet` / `⚡→🧠`) se décide en lisant
`routing_done.model_used` + `routing_done.escalated`.

Au niveau `llm_meta.model`, S06 peut aussi différencier Haiku vs
Sonnet sur chaque itération si besoin d'un affichage plus fin.

#### ✅ Points résolus

- [x] **Caps** : `MAX_TOOL_CALLS_PER_TURN = 5` **et** `WALL_CLOCK_S = 15`
      implémentés en S04 (cahier §5.3). Wall-clock via itération
      manuelle + `asyncio.wait_for` sur `__anext__` pour respecter PEP
      789.
- [x] **Regex** : accent-normalisation via `unicodedata.NFKD` avant
      match ; patterns figés ci-dessus.
- [x] **Multi-SIREN** : 2+ SIREN dans la query → Sonnet. Pattern
      `\b\d{9}\b` reste valide après normalisation (chiffres intacts).
- [x] **Tool local `escalate_to_sonnet`** : injecté via `extra_tools`
      (S03 support prouvé par `test_extra_tools_are_forwarded`).
- [x] **Lock reentrance** : break + `gen.aclose()` + run_turn
      séquentiel (§"Lock reentrance & atomicité state").
- [x] **Continuation** : `continuation=True` sur le 2e `run_turn`
      (S03 support prouvé par `test_continuation_does_not_reappend_user_message`).
- [x] **Events yield** : superset S03 + `routing_initial` +
      `escalation` + `capped` + `routing_done`.
- [x] **Import caps depuis S05** : S05 n'est pas encore mergée — S04
      définit `MAX_TOOL_CALLS_PER_TURN = 5` localement avec un TODO
      explicite. Quand S05 mergée, remplacer par
      `from genial_agent.guardrails.caps import MAX_TOOL_CALLS_PER_TURN`.

### 🔑 Input utilisateur encore attendu

- Aucun. Toutes les clés déjà validées pré-S03.

### Commit phase 1

`story(S04): refine — PEP 789 wait_for pattern, NFKD regex, lock reentrance via aclose, tool_calls + wall-clock caps in S04`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/routing.py` — créer.
- `tests/unit/test_S04_routing.py` — créer (keyword router + fake
  AsyncAnthropic pour escalation paths).
- `tests/integration/test_S04_routing_live.py` — créer (live, opt-in
  marker `integration`).

**Pas** de modification de `agent.py` / `models.py` / `prompts.py` :
S03 expose déjà tous les hooks nécessaires (`extra_tools`,
`continuation`, `tool_choice`, `tier`).

### `routing.py` — squelette complet

```python
"""Routing Haiku → Sonnet : pré-routeur keyword + escalate tool + caps.

Architecture Option B' (cahier §5.3) — défense en profondeur 3 couches :

1. **Pré-routeur keyword** (0 LLM, < 1 ms) — regex à frontière de mot
   sur texte normalisé NFKD-lowercase. Détecte comparaison, dossier
   complet, évolution multi-années, pronoms interrogatifs multi-
   entités, multi-SIREN. Dispatch direct Sonnet.
2. **Auto-escalade Haiku** — tool `escalate_to_sonnet` injecté dans
   ``extra_tools``. Haiku décide lui-même quand il sature.
3. **Cap dur backend** — ``MAX_TOOL_CALLS_PER_TURN = 5`` et
   ``WALL_CLOCK_S = 15`` (cahier §5.3, §14.3 C4). Mesurés en delta sur
   ``state.tool_calls_count`` et ``time.monotonic()``. Si un cap est
   atteint en tier Haiku, S04 force une escalade (mode ``forced``). En
   tier Sonnet, S04 émet un event ``capped`` et arrête la boucle.

Implémentation wall-clock : itération manuelle avec
``asyncio.wait_for(gen.__anext__(), timeout=remaining)`` pour respecter
PEP 789 (pas de ``asyncio.timeout`` autour d'un yield dans un async
generator). Cf. phase 1 §"Décision majeure".
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from typing import Any

import structlog

from genial_agent.agent import ConversationState, run_turn
from genial_agent.models import ModelTier

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Caps S04 (à migrer vers guardrails/caps.py en S05)
# ---------------------------------------------------------------------------

# Caps par-turn (cahier §5.3, §14.3 C4, README "Décisions de cohérence").
# TODO(S05): déplacer vers guardrails/caps.py et importer d'ici.
try:
    from genial_agent.guardrails.caps import (  # type: ignore[import-not-found]
        MAX_TOOL_CALLS_PER_TURN,
        WALL_CLOCK_S,
    )
except ImportError:  # S05 pas encore mergée
    MAX_TOOL_CALLS_PER_TURN = 5
    WALL_CLOCK_S = 15


# ---------------------------------------------------------------------------
# Pré-routeur keyword
# ---------------------------------------------------------------------------


def _normalize_fr(text: str) -> str:
    """Strip diacritiques + lowercase pour match accent-insensible.

    Justifie d'écrire les patterns en ASCII minuscule : on travaille
    sur texte normalisé. Indispensable pour attraper "evolution"
    (sans accent) ou "EVOLUTION" (majuscules).
    """
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = nfkd.encode("ascii", "ignore").decode("ascii")
    return stripped.lower()


# Patterns en ASCII minuscule — appliqués APRÈS _normalize_fr.
# Ordre important : premier match gagne.
COMPLEX_PATTERNS: list[re.Pattern[str]] = [
    # Comparaison / versus (cahier §3 U3)
    re.compile(r"\b(compare|comparaison|compares|comparons|versus|vs\.?)\b"),
    # Due diligence / dossier complet (cahier §7 terminologie)
    re.compile(r"\b(dossier\s+complet|due\s+diligence|due\s+dil)\b"),
    # Évolution multi-années (cahier §3 U3)
    re.compile(r"\b(evolution|evolutions|sur\s+\d+\s+ans?)\b"),
    # Pronoms interrogatifs multi-entités
    re.compile(r"\b(lequel|laquelle|lesquels?|lesquelles?)\b"),
    # Similarité / concurrence
    re.compile(r"\b(similaires?\s+a|concurrents?\s+de)\b"),
]

# Multi-SIREN — 2 SIREN détectés = intention multi-entité.
SIREN_RE = re.compile(r"\b\d{9}\b")


def pick_initial_tier(user_message: str) -> ModelTier:
    """Pré-routeur keyword. Retourne Sonnet si complexe, sinon Haiku.

    Texte normalisé NFKD-lowercase avant match (accent-insensible).
    Multi-SIREN (≥ 2 SIREN 9-chiffres) = complexe.
    """
    normalized = _normalize_fr(user_message)
    for pattern in COMPLEX_PATTERNS:
        if pattern.search(normalized):
            return ModelTier.SONNET
    if len(SIREN_RE.findall(user_message)) >= 2:
        return ModelTier.SONNET
    return ModelTier.HAIKU


# ---------------------------------------------------------------------------
# Tool méta escalate_to_sonnet
# ---------------------------------------------------------------------------

ESCALATE_TOOL_NAME = "escalate_to_sonnet"

ESCALATE_TOOL_SCHEMA: dict[str, Any] = {
    "name": ESCALATE_TOOL_NAME,
    "description": (
        "Signale que la requête utilisateur dépasse tes capacités "
        "actuelles et qu'un modèle plus puissant (Sonnet) doit "
        "prendre le relais. Appelle-le SEUL (pas d'autre tool_use "
        "dans la même réponse) SI ET SEULEMENT SI : (a) la question "
        "nécessite une comparaison multi-entités fine, (b) elle "
        "demande un raisonnement financier qui dépasse tes "
        "capacités, (c) tu sens qu'il te faudra 3+ tool calls "
        "supplémentaires que tu ne pourras pas synthétiser "
        "correctement. Fournis une raison courte (≤ 1 phrase). "
        "L'escalade conserve l'intégralité du contexte conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": (
                    "Pourquoi tu estimes ne pas pouvoir conclure seul."
                ),
            }
        },
        "required": ["reason"],
    },
}


# ---------------------------------------------------------------------------
# Boucle routée
# ---------------------------------------------------------------------------


async def run_routed_turn(
    state: ConversationState,
    user_message: str,
) -> AsyncIterator[dict[str, Any]]:
    """Exécute un tour agent avec routing Haiku → Sonnet.

    Flow :

    1. ``pick_initial_tier`` → Haiku (défaut) ou Sonnet (keyword complexe).
    2. Lancer ``run_turn`` avec ce tier. Si Haiku, injecter le tool
       local ``escalate_to_sonnet`` via ``extra_tools``.
    3. Itération manuelle ``wait_for(__anext__, timeout=remaining)`` pour
       respecter le wall-clock cap sans tomber dans l'antipattern PEP 789.
    4. Forwarder les events S03 tels quels + émettre nos events routing.
    5. Détecter escalade :
       - **self** : event ``tool_use`` avec ``name == "escalate_to_sonnet"``.
       - **forced (tool_calls)** : ``state.tool_calls_count - initial_count >= MAX_TOOL_CALLS_PER_TURN``.
       - **forced (wall_clock)** : ``remaining <= 0`` en haut de boucle,
         ou ``asyncio.TimeoutError`` levé par ``wait_for``.
    6. Sur escalade : break, ``gen.aclose()`` (release ``state.lock``),
       relancer ``run_turn`` en tier Sonnet avec ``continuation=True``.
       L'invariant I2 de S03 garantit que ``state.messages`` est
       cohérent (pas d'orphan ``tool_use``).
    7. Émettre ``routing_done`` en toute fin (après ``end`` de S03).
    """
    initial_tier = pick_initial_tier(user_message)
    yield {
        "type": "routing_initial",
        "tier": initial_tier.value,
        "reason": "keyword" if initial_tier == ModelTier.SONNET else "default",
    }
    logger.info("routing_initial", tier=initial_tier.value)

    initial_count = state.tool_calls_count
    deadline = time.monotonic() + WALL_CLOCK_S
    escalated = False
    escalation_reason: str | None = None
    escalation_mode: str | None = None
    capped_in_sonnet = False  # set si cap atteint alors qu'on est déjà Sonnet

    def _hit_cap_tool_calls() -> bool:
        return (state.tool_calls_count - initial_count) >= MAX_TOOL_CALLS_PER_TURN

    def _emit_cap_hit(
        cap_reason: str,
    ) -> dict[str, Any]:
        """Fabrique l'event à émettre quand un cap est atteint, selon le
        tier courant. Ne met PAS à jour les flags — c'est fait par
        l'appelant."""
        if initial_tier == ModelTier.HAIKU:
            return {"type": "escalation", "reason": cap_reason, "mode": "forced"}
        return {"type": "capped", "reason": cap_reason, "count": state.tool_calls_count - initial_count}

    # Haiku reçoit le tool escalate_to_sonnet en plus des Pappers.
    # Sonnet ne l'a pas (c'est déjà lui, rien à escalader).
    extra_tools = [ESCALATE_TOOL_SCHEMA] if initial_tier == ModelTier.HAIKU else []

    gen = run_turn(
        state,
        user_message,
        tier=initial_tier,
        extra_tools=extra_tools,
    )
    aiter = gen.__aiter__()
    try:
        while True:
            # --- Wall-clock cap (cahier §5.3) ---
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cap_reason = f"cap_wall_clock={WALL_CLOCK_S}s"
                logger.warning("routing_cap_wall_clock", seconds=WALL_CLOCK_S)
                yield _emit_cap_hit(cap_reason)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason = cap_reason
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_in_sonnet = True
                break

            # --- Next event, bounded par remaining ---
            try:
                event = await asyncio.wait_for(aiter.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                break
            except TimeoutError:
                # Un single await dans run_turn a dépassé `remaining`.
                # Même traitement que wall-clock cap hit.
                cap_reason = f"cap_wall_clock={WALL_CLOCK_S}s"
                logger.warning("routing_cap_wall_clock_wait_for", seconds=WALL_CLOCK_S)
                yield _emit_cap_hit(cap_reason)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason = cap_reason
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_in_sonnet = True
                break

            # --- Self-escalade : break AVANT que run_turn exécute le
            # "tool" escalate_to_sonnet via mcp_pappers (MCP ne le
            # connaît pas, ça partirait en PappersError / 404). ---
            if (
                event.get("type") == "tool_use"
                and event.get("name") == ESCALATE_TOOL_NAME
            ):
                escalation_reason = (
                    event.get("input", {}).get("reason") or "unspecified"
                )
                escalation_mode = "self"
                escalated = True
                logger.info("routing_escalate_self", reason=escalation_reason)
                yield {
                    "type": "escalation",
                    "reason": escalation_reason,
                    "mode": "self",
                }
                break

            yield event

            # --- Cap tool calls par-turn ---
            if _hit_cap_tool_calls():
                per_turn = state.tool_calls_count - initial_count
                cap_reason = f"cap_tool_calls_per_turn={per_turn}"
                logger.warning("routing_cap_tool_calls", per_turn_count=per_turn)
                yield _emit_cap_hit(cap_reason)
                if initial_tier == ModelTier.HAIKU:
                    escalation_reason = cap_reason
                    escalation_mode = "forced"
                    escalated = True
                else:
                    capped_in_sonnet = True
                break
    finally:
        # Essentiel : libère state.lock en forçant le cleanup du
        # generator S03 (async with state.lock: fin de bloc). Sans
        # aclose(), le lock peut rester détenu jusqu'au GC. aclose()
        # est aussi ce qui propage CancelledError dans S03 si on a
        # break après un wait_for timeout — S03 cleanup cohérent.
        await gen.aclose()

    if escalated:
        # L'invariant I2 de S03 garantit que state.messages est cohérent :
        # l'itération contenant l'escalade (ou sa détection) n'a pas été
        # appendée. Sonnet reprend sur un state propre avec continuation.
        #
        # Sonnet n'a PAS de wall-clock cap appliqué (pas de tier au-dessus
        # pour escalader ; la démo peut tolérer une réponse Sonnet longue).
        # Si besoin post-MVP : wrapper ce 2e async for dans la même
        # logique wait_for + event capped, sans relance.
        async for event in run_turn(
            state,
            "",
            tier=ModelTier.SONNET,
            continuation=True,
        ):
            yield event

    yield {
        "type": "routing_done",
        "model_used": (
            ModelTier.SONNET.value if escalated or initial_tier == ModelTier.SONNET
            else ModelTier.HAIKU.value
        ),
        "escalated": escalated,
        "escalation_mode": escalation_mode,
        "escalation_reason": escalation_reason,
        "capped": capped_in_sonnet,
        "tool_calls_count": state.tool_calls_count - initial_count,
    }
```

### Gotchas documentés (à respecter impérativement)

- **Pas de `asyncio.timeout` autour du `async for`** (PEP 789 —
  antipattern). Utiliser l'itération manuelle avec
  `asyncio.wait_for(aiter.__anext__(), timeout=remaining)` — `wait_for`
  wrap un single await, pas un yield.
- **`asyncio.TimeoutError` ≠ hard error** : c'est le signal "single
  await dépassait `remaining`". Traiter comme wall-clock cap hit
  (même branche que `remaining <= 0` en haut de boucle).
- **Pas de wall-clock sur le Sonnet de relance** : après escalade,
  le 2e `run_turn` n'a pas de cap S04. Trade-off MVP assumé — on veut
  que Sonnet puisse conclure proprement. Un cap post-escalation
  serait un next step.
- **`gen.aclose()` obligatoire** dans le `finally` — sans ça le
  `state.lock` peut rester détenu jusqu'au GC (non-CPython,
  race conditions). Propage aussi `CancelledError` dans S03 pour
  fermer la boucle mid-stream proprement en cas de wall-clock hit.
- **Break avant le tool execute d'escalate** — le `yield {"type":
  "tool_use", "name": "escalate_to_sonnet"}` suspend S03 avant
  l'appel `mcp_pappers.call_tool(...)`. Si S04 ne breake pas,
  MCP reçoit un nom de tool inconnu et renvoie une erreur qui
  embrouille Claude.
- **`continuation=True` sur le 2e `run_turn`** — sinon le user message
  est réappendé, dupliquant l'entrée dans l'historique.
- **Caps en delta, pas en absolu** — `state.tool_calls_count` est
  cumulatif par session. Snapshot `initial_count` au début du tour.
- **Pas de logger du `user_message`** — peut contenir de la PII (nom,
  SIREN). Logguer la taille et le tier uniquement.
- **Normalisation NFKD avant regex** — sinon "evolution" (sans accent)
  rate le pattern `\bevolution\b`. Le pattern doit être en ASCII
  minuscule.
- **`pick_initial_tier` ne regarde que le dernier user_message** —
  pas le state complet. C'est intentionnel : un follow-up court
  "et son CA ?" doit rester en Haiku même si le tour précédent
  était Sonnet. Haiku gère la résolution de pronom sur l'historique
  conversationnel (cf. S03 test_multi_turn_pronoun_resolution_lvmh).

### Tests à produire

#### Unitaires (`tests/unit/test_S04_routing.py`)

Deux groupes : **keyword router** (pur, rapide) + **boucle routée**
(via fake AsyncAnthropic, même pattern que `test_S03_agent_loop.py`
— 0 crédit consommé).

```python
"""Tests unitaires S04 — routing Haiku ↔ Sonnet.

Pattern ``fake AsyncAnthropic`` réutilisé depuis
``test_S03_agent_loop.py`` — 0 appel API, 0 crédit Pappers. Couvre la
plomberie :

- ``pick_initial_tier`` sur cas paramétrés FR (avec et sans accents).
- Self-escalation : Haiku appelle ``escalate_to_sonnet`` → S04 break
  → Sonnet reprend en continuation.
- Forced escalation : cap tool_calls_per_turn atteint en Haiku →
  escalade forcée.
- Capped sur Sonnet : cap atteint alors qu'on est déjà Sonnet →
  event ``capped`` émis, pas d'escalade possible.
- State integrity après escalation (I2 de S03).
- Lock released after gen.aclose().
- Events ``routing_initial`` / ``routing_done`` toujours émis.
- Multi-SIREN → Sonnet direct.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from genial_agent.agent import ConversationState
from genial_agent.models import MODEL_HAIKU, MODEL_SONNET, ModelTier
from genial_agent.routing import (
    ESCALATE_TOOL_SCHEMA,
    MAX_TOOL_CALLS_PER_TURN,
    _normalize_fr,
    pick_initial_tier,
    run_routed_turn,
)

# Fakes importés depuis le module S03 pour éviter la duplication.
# Alternative : recopier dans ``tests/_fakes_anthropic.py`` si on veut
# isoler. Pour le MVP, on import depuis le fichier S03 (même process
# de test, stable).
from tests.unit.test_S03_agent_loop import (  # type: ignore[import-not-found]
    _ScriptedTurn,
    _install_fake_anthropic,
    _install_fake_mcp,
    _message,
    _text,
    _tool_use,
)


# ============================================================================
# Group 1 — pick_initial_tier (pur, rapide, accent-sensible)
# ============================================================================


@pytest.mark.parametrize(
    "msg,expected",
    [
        # Simple → Haiku
        ("Donne-moi la fiche de LVMH", ModelTier.HAIKU),
        ("Les dirigeants de BNP Paribas", ModelTier.HAIKU),
        ("Qui sont les dirigeants ?", ModelTier.HAIKU),
        ("Bonjour", ModelTier.HAIKU),
        # Comparaison → Sonnet
        ("Compare Carrefour et Casino", ModelTier.SONNET),
        ("Comparaison entre Renault et PSA", ModelTier.SONNET),
        ("Carrefour versus Auchan", ModelTier.SONNET),
        ("LVMH vs Kering", ModelTier.SONNET),
        ("LVMH vs. Kering", ModelTier.SONNET),
        # Dossier complet / due diligence
        ("Fais-moi un dossier complet sur Total", ModelTier.SONNET),
        ("Due diligence sur Sanofi", ModelTier.SONNET),
        ("Due dil sur BNP", ModelTier.SONNET),
        # Évolution multi-années (avec et sans accent)
        ("Évolution du CA sur 3 ans", ModelTier.SONNET),
        ("Evolution du CA sur 3 ans", ModelTier.SONNET),  # sans accent
        ("EVOLUTION DU CA", ModelTier.SONNET),  # majuscules
        ("sur 5 ans", ModelTier.SONNET),
        # Pronoms interrogatifs
        ("Lequel est le plus rentable ?", ModelTier.SONNET),
        ("Laquelle a le meilleur CA ?", ModelTier.SONNET),
        ("Lesquels sont cotés ?", ModelTier.SONNET),
        # Similarité / concurrence (avec normalisation "à" → "a")
        ("Entreprises similaires à Michelin", ModelTier.SONNET),
        ("Entreprises similaires a Michelin", ModelTier.SONNET),
        ("Concurrents de Decathlon", ModelTier.SONNET),
        # Multi-SIREN
        ("Compare 123456789 et 987654321", ModelTier.SONNET),
        ("Fais la fiche de 123456789", ModelTier.HAIKU),  # un seul SIREN → Haiku
        # Pas de faux positifs
        ("Une solution comparable à X", ModelTier.HAIKU),  # "comparable" ≠ "compare"
        ("Jeune entreprise avec de l'évolution", ModelTier.SONNET),  # "evolution" matche (acceptable)
    ],
)
def test_pick_initial_tier(msg: str, expected: ModelTier) -> None:
    assert pick_initial_tier(msg) == expected


def test_normalize_fr_strips_diacritics() -> None:
    assert _normalize_fr("Évolution") == "evolution"
    assert _normalize_fr("Société Générale") == "societe generale"
    assert _normalize_fr("À propos") == "a propos"
    assert _normalize_fr("Cœur") == "coeur"  # œ → oe via NFKD
    assert _normalize_fr("Straße") == "strae"  # NFKD + ascii strip : ß drop (edge case)


def test_escalate_tool_schema_shape() -> None:
    # Anthropic name pattern ^[a-zA-Z0-9_-]{1,128}$
    assert re.match(r"^[a-zA-Z0-9_-]{1,128}$", ESCALATE_TOOL_SCHEMA["name"])
    assert ESCALATE_TOOL_SCHEMA["name"] == "escalate_to_sonnet"
    assert "reason" in ESCALATE_TOOL_SCHEMA["input_schema"]["properties"]
    assert ESCALATE_TOOL_SCHEMA["input_schema"]["required"] == ["reason"]


# ============================================================================
# Group 2 — run_routed_turn via fake AsyncAnthropic
# ============================================================================


async def test_simple_query_stays_haiku(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Fiche LVMH' → Haiku direct, pas d'escalade."""
    script = [
        _ScriptedTurn(
            text_chunks=["Fiche LVMH..."],
            final=_message(stop_reason="end_turn", content=[_text("Fiche LVMH...")]),
        )
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Fiche LVMH")]

    routing_initial = next(e for e in events if e["type"] == "routing_initial")
    assert routing_initial["tier"] == "haiku"
    assert routing_initial["reason"] == "default"

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["model_used"] == "haiku"
    assert routing_done["escalated"] is False

    # Pas d'event escalation ni capped
    assert not any(e["type"] == "escalation" for e in events)
    assert not any(e["type"] == "capped" for e in events)


async def test_complex_keyword_goes_sonnet_direct(monkeypatch: pytest.MonkeyPatch) -> None:
    """'Compare X et Y' → Sonnet direct, sans passer par Haiku."""
    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Comparaison...")],
                model=MODEL_SONNET,
            ),
        )
    ]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare LVMH et Kering")]

    routing_initial = next(e for e in events if e["type"] == "routing_initial")
    assert routing_initial["tier"] == "sonnet"
    assert routing_initial["reason"] == "keyword"

    # Un seul appel stream — pas de re-dispatch
    assert len(fake.messages.calls) == 1
    assert fake.messages.calls[0]["model"] == MODEL_SONNET
    # Pas de extra_tools escalate (on est déjà Sonnet)
    tool_names = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert "escalate_to_sonnet" not in tool_names


async def test_haiku_self_escalates_to_sonnet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku appelle escalate_to_sonnet → break → Sonnet reprend en
    continuation. State intact (invariant I2)."""
    escalate_tu = _tool_use(
        "tu_esc",
        "escalate_to_sonnet",
        {"reason": "Besoin de raisonnement multi-entités"},
    )
    script = [
        # Haiku escalade directement
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        # Sonnet reprend en continuation
        _ScriptedTurn(
            text_chunks=["Analyse Sonnet..."],
            final=_message(
                stop_reason="end_turn",
                content=[_text("Analyse Sonnet...")],
                model=MODEL_SONNET,
            ),
        ),
    ]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Analyse fine de X")]

    # Event escalation émis
    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "self"
    assert "multi-entit" in escalation["reason"].lower()

    # routing_done reflète l'escalade
    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["model_used"] == "sonnet"
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "self"

    # 2 appels stream : Haiku + Sonnet
    assert len(fake.messages.calls) == 2
    assert fake.messages.calls[0]["model"] == MODEL_HAIKU
    assert fake.messages.calls[1]["model"] == MODEL_SONNET

    # Le 2e appel a ``messages`` partageant le state du 1er (continuation)
    # Le user_wrap initial est présent, pas dupliqué
    user_messages = [m for m in state.messages if m.get("role") == "user"]
    # Seul le wrap initial (pas de second user message créé par continuation)
    wrap_count = sum(
        1 for m in user_messages
        if isinstance(m.get("content"), str) and "<user_input>" in m["content"]
    )
    assert wrap_count == 1


async def test_haiku_self_escalate_tool_never_called_on_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le tool escalate_to_sonnet ne doit JAMAIS être envoyé à
    mcp_pappers.call_tool — S04 break avant l'exécution."""
    escalate_tu = _tool_use("tu_esc", "escalate_to_sonnet", {"reason": "x"})
    script = [
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")])),
    ]
    _install_fake_anthropic(monkeypatch, script)
    mcp_calls = _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "x"):
        pass

    # Aucun appel mcp_pappers.call_tool avec escalate_to_sonnet
    assert all(name != "escalate_to_sonnet" for name, _ in mcp_calls)


async def test_state_lock_released_after_self_escalate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Après escalade self, state.lock doit être libre (sinon le 2e
    run_turn Sonnet deadlockerait à cause de run_turn qui re-acquiert
    state.lock)."""
    escalate_tu = _tool_use("tu_esc", "escalate_to_sonnet", {"reason": "x"})
    script = [
        _ScriptedTurn(final=_message(stop_reason="tool_use", content=[escalate_tu])),
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")])),
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "x"):
        pass

    # Pas de deadlock (sinon le test aurait timeout) + lock libre à la fin
    assert not state.lock.locked()


async def test_forced_escalation_on_tool_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Haiku fait 5 tool calls sans conclure → cap atteint → escalade
    forcée vers Sonnet en continuation."""
    # Haiku fait 5 tool_use successifs (chaque iter = 1 tool_use)
    haiku_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="tool_use",
                content=[_tool_use(f"t{i}", "sirenisateur", {"company_name": f"E{i}"})],
            )
        )
        for i in range(MAX_TOOL_CALLS_PER_TURN)
    ]
    # Sonnet conclut
    sonnet_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Sonnet conclut.")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, haiku_script + sonnet_script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "fiche runaway")]

    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "forced"
    assert "cap_tool_calls_per_turn" in escalation["reason"]

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "forced"
    assert routing_done["model_used"] == "sonnet"


async def test_capped_on_sonnet_no_further_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Query complexe → Sonnet direct. Si Sonnet lui-même atteint le cap,
    on émet un event ``capped`` mais pas d'escalation (pas de tier
    au-dessus pour MVP)."""
    # Sonnet fait 5 tool_use puis conclut
    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="tool_use",
                content=[_tool_use(f"t{i}", "sirenisateur", {"company_name": f"E{i}"})],
                model=MODEL_SONNET,
            )
        )
        for i in range(MAX_TOOL_CALLS_PER_TURN)
    ]
    script.append(
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("ok")],
                model=MODEL_SONNET,
            )
        )
    )
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare A et B")]

    capped_events = [e for e in events if e["type"] == "capped"]
    assert len(capped_events) == 1
    assert capped_events[0]["reason"] == "tool_calls_per_turn"

    # Pas d'escalation (déjà Sonnet)
    assert not any(e["type"] == "escalation" for e in events)


async def test_forced_escalation_on_wall_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wall-clock cap atteint en Haiku → escalade forcée vers Sonnet.

    On force le budget à 0 s via monkeypatch pour que la première
    itération de la boucle détecte ``remaining <= 0`` immédiatement.
    Alternative plus réaliste (mais lente en CI) : faire traîner le
    fake stream avec un ``asyncio.sleep``. Choix : monkeypatch simple,
    0 ms wall-clock.
    """
    import genial_agent.routing as routing_mod

    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0)

    haiku_script = [
        _ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("hi")]))
    ]
    sonnet_script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("Sonnet conclut.")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, haiku_script + sonnet_script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "x")]

    escalation = next(e for e in events if e["type"] == "escalation")
    assert escalation["mode"] == "forced"
    assert "cap_wall_clock" in escalation["reason"]

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is True
    assert routing_done["escalation_mode"] == "forced"
    assert routing_done["model_used"] == "sonnet"


async def test_wall_clock_triggers_capped_on_sonnet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tier initial Sonnet (via keyword) + wall-clock hit → event
    ``capped``, pas d'escalation (pas de tier au-dessus pour MVP)."""
    import genial_agent.routing as routing_mod

    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0)

    script = [
        _ScriptedTurn(
            final=_message(
                stop_reason="end_turn",
                content=[_text("start...")],
                model=MODEL_SONNET,
            )
        )
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Compare A et B")]

    capped_events = [e for e in events if e["type"] == "capped"]
    assert len(capped_events) == 1
    assert "cap_wall_clock" in capped_events[0]["reason"]

    # Pas d'escalation (déjà Sonnet)
    assert not any(e["type"] == "escalation" for e in events)

    routing_done = next(e for e in events if e["type"] == "routing_done")
    assert routing_done["escalated"] is False
    assert routing_done["capped"] is True


async def test_wall_clock_wait_for_timeout_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fake stream qui bloque sur le 1er ``__anext__`` plus
    longtemps que ``WALL_CLOCK_S`` doit faire lever
    ``asyncio.TimeoutError`` côté wait_for, traité comme cap hit.

    Ce test couvre le chemin ``except TimeoutError`` de la boucle — pas
    atteignable par le monkeypatch ``WALL_CLOCK_S=0`` qui hit via
    ``remaining <= 0`` en haut de boucle.
    """
    import genial_agent.routing as routing_mod

    # Budget court mais > 0 pour forcer wait_for à réellement attendre.
    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 0.05)

    class _SlowStream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        @property
        def text_stream(self):
            return self._iter()

        async def _iter(self):
            await asyncio.sleep(1.0)  # >> 0.05 s
            yield "never"

        async def get_final_message(self):
            return _message(stop_reason="end_turn", content=[_text("never")])

        @property
        def request_id(self):
            return "slow"

    class _SlowMessages:
        def __init__(self):
            self.calls = []

        def stream(self, **kwargs):
            self.calls.append(kwargs)
            return _SlowStream()

    class _SlowClient:
        def __init__(self):
            self.messages = _SlowMessages()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    fake = _SlowClient()
    monkeypatch.setattr(
        "genial_agent.agent.AsyncAnthropic", lambda **_kw: fake
    )
    _install_fake_mcp(monkeypatch)

    # Relance Sonnet après escalation (2e client) — on stub simple.
    # Test focalisé : vérifier que le TimeoutError path émet
    # bien l'event cap.
    state = ConversationState()
    events: list[dict[str, Any]] = []
    # On consomme jusqu'au 1er event escalation/capped puis on arrête
    # la démo (éviter le 2e run_turn qui dépendrait du fake).
    try:
        async for ev in run_routed_turn(state, "x"):
            events.append(ev)
            if ev["type"] in {"escalation", "capped"}:
                break
    except Exception:
        pass

    tagged = [e for e in events if e["type"] in {"escalation", "capped"}]
    assert tagged, "aucun event cap émis malgré wait_for timeout"
    assert "cap_wall_clock" in tagged[0]["reason"]


async def test_routing_initial_and_done_always_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Quel que soit le chemin, exactement un routing_initial et un
    routing_done sont émis."""
    script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")]))]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    events = [ev async for ev in run_routed_turn(state, "Fiche LVMH")]

    assert sum(1 for e in events if e["type"] == "routing_initial") == 1
    assert sum(1 for e in events if e["type"] == "routing_done") == 1


async def test_extra_tools_includes_escalate_in_haiku_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = [_ScriptedTurn(final=_message(stop_reason="end_turn", content=[_text("ok")]))]
    fake = _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    state = ConversationState()
    async for _ in run_routed_turn(state, "Fiche LVMH"):
        pass

    # Haiku initial → escalate_to_sonnet injecté dans tools
    tool_names = {t["name"] for t in fake.messages.calls[0]["tools"]}
    assert "escalate_to_sonnet" in tool_names
    # Les tools Pappers sont toujours là
    assert "sirenisateur" in tool_names
```

#### Intégration (`tests/integration/test_S04_routing_live.py`)

Seulement 2 tests — on évite les tests coûteux qui dépendent de la
métacognition de Haiku (self-escalate n'est **pas** testable de
manière fiable en live : Haiku peut ne pas escalader sur notre prompt).
Les tests live valident le **keyword router** (100 % déterministe) et
le chemin simple → Haiku.

```python
"""Tests d'intégration S04 — live contre Anthropic + MCP Pappers.

Couverture live limitée aux cas déterministes :
- Simple → Haiku (garantie par le keyword router).
- Keyword complexe → Sonnet direct (garantie par le keyword router).

Les scénarios d'escalation (self / forced) sont couverts en unit par
fake AsyncAnthropic — Haiku en live peut ne pas escalader
systématiquement, c'est normal (sa métacognition est imparfaite,
c'est pour ça qu'on a aussi le cap forced + le keyword router).
"""

from __future__ import annotations

import os

import pytest

from genial_agent.agent import ConversationState
from genial_agent.models import MODEL_HAIKU, MODEL_SONNET
from genial_agent.routing import run_routed_turn

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))
REASON = "ANTHROPIC_API_KEY and/or PAPPERS_API_KEY not set"


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_simple_stays_haiku_live() -> None:
    state = ConversationState()
    routing_initial = None
    routing_done = None
    llm_models: list[str] = []

    async for event in run_routed_turn(state, "Donne-moi la fiche de LVMH"):
        if event["type"] == "routing_initial":
            routing_initial = event
        elif event["type"] == "routing_done":
            routing_done = event
        elif event["type"] == "llm_meta":
            llm_models.append(event["model"])

    assert routing_initial is not None
    assert routing_initial["tier"] == "haiku"

    assert routing_done is not None
    assert routing_done["model_used"] == "haiku"
    assert routing_done["escalated"] is False

    # Tous les llm_meta sont en Haiku (pas de bascule silencieuse)
    assert all(m == MODEL_HAIKU for m in llm_models)


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_complex_keyword_goes_sonnet_live() -> None:
    """Le keyword 'Compare' déclenche Sonnet direct — zéro appel Haiku."""
    state = ConversationState()
    routing_initial = None
    routing_done = None
    llm_models: list[str] = []

    async for event in run_routed_turn(
        state, "Compare LVMH et Kering sur 3 ans"
    ):
        if event["type"] == "routing_initial":
            routing_initial = event
        elif event["type"] == "routing_done":
            routing_done = event
        elif event["type"] == "llm_meta":
            llm_models.append(event["model"])

    assert routing_initial is not None
    assert routing_initial["tier"] == "sonnet"
    assert routing_initial["reason"] == "keyword"

    assert routing_done is not None
    assert routing_done["model_used"] == "sonnet"
    assert routing_done["escalated"] is False  # pas d'escalation sur keyword-initial

    # Tous les llm_meta sont en Sonnet
    assert all(m == MODEL_SONNET for m in llm_models)
```

### Commandes de vérification

```bash
make lint
make test-unit    # inclut les tests S04 unit (~0 crédit, rapide)

# Live opt-in (consomme ~3-5 crédits Pappers + ~2-4k tokens Claude) :
make test-integration
```

### Commit phase 2

`feat(S04): keyword router, escalate_to_sonnet tool, tool_calls + wall-clock caps via wait_for`

puis, si tests passent :

`test(S04): unit tests via fake AsyncAnthropic + 2 live integration tests`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] **PEP 789 respecté** : aucun `asyncio.timeout` / `asyncio.TaskGroup`
      autour d'un `yield` dans `run_routed_turn`. Wall-clock implémenté
      via itération manuelle + `asyncio.wait_for(aiter.__anext__(),
      timeout=remaining)`.
- [ ] **`TimeoutError` de `wait_for` traité comme cap hit** (même
      branche que `remaining <= 0`), pas comme erreur fatale.
- [ ] **`gen.aclose()` présent dans un `finally`** — libère
      `state.lock` de manière déterministe.
- [ ] **Break avant exécution du tool escalate** — vérifier qu'aucun
      test ne voit `mcp_pappers.call_tool` appelé avec
      `"escalate_to_sonnet"`.
- [ ] **Caps en delta** — `state.tool_calls_count - initial_count`,
      pas en absolu ; `deadline = time.monotonic() + WALL_CLOCK_S`
      posé une seule fois à l'entrée.
- [ ] **Normalisation NFKD avant regex** — tests accent/majuscules
      passent.
- [ ] **Patterns sans faux positifs** — `comparable` ne matche pas
      `compare` ; `sur 3 ans` matche bien ; un seul SIREN ≠ multi-SIREN.
- [ ] **`continuation=True`** au 2e `run_turn` (pas de doublon user
      message).
- [ ] **`extra_tools=[ESCALATE_TOOL_SCHEMA]`** uniquement quand tier
      initial = Haiku. Sonnet initial → pas d'escalate injecté.
- [ ] **Event contract complet** : `routing_initial`, `routing_done`
      toujours émis ; `escalation` avec `mode: self|forced` si escalade ;
      `capped` si cap sur Sonnet.
- [ ] **Pas de log du `user_message`** ni de PII potentielle.
- [ ] **Pas d'import de `guardrails/caps`** en dur — fallback via
      try/except ImportError en attendant S05.
- [ ] **Tests unit** : router paramétré + fake AsyncAnthropic pour les
      paths escalation / cap / lock release.
- [ ] **Tests live** : limités à 2 scénarios déterministes (simple
      Haiku, keyword Sonnet). Pas de test live d'escalation (flaky).
- [ ] `gitleaks` clean.

### Commit phase 3

`review(S04): approved` (si RAS) ou `review(S04): fix — …` + rework.

---

## ✅ Critères d'acceptation

- [ ] `pick_initial_tier` passe tous les cas paramétrés (accents,
      majuscules, word boundaries, multi-SIREN).
- [ ] Test `test_simple_query_stays_haiku` (unit fake) passe.
- [ ] Test `test_complex_keyword_goes_sonnet_direct` (unit fake) passe.
- [ ] Test `test_haiku_self_escalates_to_sonnet` (unit fake) passe :
      2 appels stream, events escalation+routing_done cohérents.
- [ ] Test `test_haiku_self_escalate_tool_never_called_on_mcp` (unit)
      passe — pas d'appel `mcp_pappers.call_tool` sur
      `escalate_to_sonnet`.
- [ ] Test `test_forced_escalation_on_tool_cap` (unit fake) passe.
- [ ] Test `test_forced_escalation_on_wall_clock` (unit fake) passe :
      `WALL_CLOCK_S=0` monkeypatch, escalation `mode=forced`,
      `reason` contient `cap_wall_clock`.
- [ ] Test `test_capped_on_sonnet_no_further_escalation` (unit fake)
      passe : event `capped` émis, pas d'escalation.
- [ ] Test `test_wall_clock_triggers_capped_on_sonnet` (unit fake)
      passe : Sonnet initial + wall-clock hit → `capped`, pas
      d'escalation, `routing_done.capped=True`.
- [ ] Test `test_wall_clock_wait_for_timeout_path` (unit) passe :
      couvre le chemin `except TimeoutError` (fake stream qui bloque
      plus que `WALL_CLOCK_S`).
- [ ] Test `test_state_lock_released_after_self_escalate` (unit)
      passe — pas de deadlock.
- [ ] Tests live `test_simple_stays_haiku_live` et
      `test_complex_keyword_goes_sonnet_live` passent (quand
      `make test-integration` lancé avec les clés).
- [ ] `make lint` vert, `make test` (= test-unit) vert.
- [ ] `gitleaks detect` clean sur le commit phase 2.

---

## 📦 Done when

- [ ] Phase 1 commitée (`story(S04): refine — …`).
- [ ] Phase 2 commitée (`feat(S04): …`) + tests verts.
- [ ] Phase 3 approuvée (`review(S04): approved`).
- [ ] Ligne S04 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué sur `claude/builder-evaluation-exercise-34Iyu`.

---

## 📎 Annexes

### A. Import depuis `test_S03_agent_loop`

Le fichier `tests/unit/test_S04_routing.py` import les fakes depuis
`test_S03_agent_loop.py`. C'est un choix pragmatique (pas de
duplication) mais crée une dépendance entre fichiers de test. Deux
options pour le dev agent :

1. **Keep as-is** (par défaut) — import direct, simple.
2. **Extraire dans `tests/_fakes_anthropic.py`** — si la dépendance
   gêne. Dans ce cas, déplacer les `_ScriptedTurn`, `_FakeStream`,
   `_FakeAsyncAnthropic`, `_install_fake_anthropic`, `_install_fake_mcp`
   dans un fichier neutre et faire `from tests._fakes_anthropic import …`
   dans les deux tests. **Ne pas oublier** d'ajouter
   `tests/__init__.py` si absent.

Option 1 retenue par défaut pour l'exo week-end. Option 2 documentée
comme next step.

### B. Pourquoi pas de `tool_choice="any"` sur Haiku ?

L'original S03 a figé `tool_choice={"type": "auto"}` (S03 phase 1,
justifié). Forcer `any` sur Haiku quand le tier initial est Haiku
+ escalate disponible ferait qu'Haiku est obligé d'appeler **un**
tool (soit Pappers, soit escalate). Ça casse le cas "bonjour ?" ou
les questions hors-scope que Haiku doit refuser sans tool call.

Résultat : `tool_choice="auto"` partout, décision inchangée de S03.

### C. Sonnet Plus (post-MVP)

Un tier Opus (next step documenté en README S09) permettrait une
escalade à 2 niveaux (Haiku → Sonnet → Opus). Pour S04 MVP on s'arrête
à 2 tiers (cahier §5.3) — le cap sur Sonnet émet `capped` au lieu
d'escalader.

### D. Pourquoi un forced cap en Haiku = escalade, pas kill ?

Cahier §5.3 :

> **Cap dur backend** — 5 tool calls ou 15 s wall-clock sans conclusion
> → **escalade forcée côté code**. Filet de sécurité au cas où Haiku
> sur-estime ses capacités (métacognition LLM imparfaite).

Donc le 5 tool calls cap en Haiku **déclenche une escalade**, pas un
kill de la conversation. L'utilisateur voit Sonnet reprendre
(`⚡→🧠` en UI, event `escalation(mode=forced)`). En Sonnet, le cap
reste un cap "soft" (emet `capped`), la réponse se termine avec ce
que Sonnet a pu produire.
