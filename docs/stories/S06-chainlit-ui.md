# S06 — UI Chainlit

> **Statut** : ⬜ à faire
> **Durée estimée** : 1 h 30
> **Parallélisable avec** : S05

---

## 📍 Contexte

L'interface utilisateur. Chainlit fournit le chat, on lui ajoute : empty
state avec starters, badges modèle, SIREN cliquables, dates bilan,
bannière contexte multi-turn, score confiance, fallbacks, footer RGPD.

Sources de vérité :
- `docs/cahier-des-charges.md` §16 (détails UX/UI).
- Stories S03 (agent core), S04 (routing).

---

## 🔒 Prérequis

- [ ] S01 → S03 terminées (S04 idéalement aussi).

## 🔑 Inputs utilisateur requis

- Aucun nouveau.

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/app.py` (entry Chainlit).
- Empty state avec 4 starters cliquables.
- Badges `⚡ Haiku` / `🧠 Sonnet` / `⚡→🧠` en metadata sur chaque réponse.
- Post-processing : SIREN → lien cliquable vers `pappers.fr/entreprise/{siren}`.
- Affichage date de bilan à côté des chiffres (conditionné au format de
  sortie de l'agent).
- **Bannière "Entité active: NOM (SIREN ...)" épinglée** (cahier §16.2)
  — extraction automatique après chaque turn, mise à jour via un message
  dédié stocké dans `cl.user_session`.
- Badge score confiance (vert/orange/rouge) après critic async.
- **Task critic tracée dans `cl.user_session`** (pas d'orphan
  `asyncio.create_task`) — annulation propre à la fin de la session.
- **Tool results rattachés à leur `cl.Step`** (input + output visibles
  quand l'utilisateur déplie).
- Footer RGPD permanent.
- États d'erreur UX : MCP KO, cap atteint, crédits bas.

### Hors scope

- Audio brief (S10).
- `/health` et `/stats` endpoints (S07).

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Version actuelle Chainlit en 2026 : v1.x ou v2.x ? API des
      `cl.Starter`, `cl.ChatSettings`, `cl.Message`, `cl.Step`.
- [ ] Comment ajouter des metadata / badges custom sur un message
      (via `cl.Message(author=..., content=..., elements=[...])` ou
      via markdown).
- [ ] Pattern recommandé pour afficher un statut en haut du chat
      (bannière) : `cl.on_chat_start` avec un message système épinglé,
      ou `cl.ChatProfile`.
- [ ] Méthode pour streamer en asynchrone avec Chainlit (`msg.stream_token()`).
- [ ] Comment afficher un footer permanent (probablement via markdown
      ou via chainlit config `public/`).
- [ ] Vérifier la doc officielle : https://docs.chainlit.io.

### Points à résoudre

- [ ] Multi-turn : stocker l'entité active dans `cl.user_session` ou dans
      le `ConversationState`. Reco : **dans `ConversationState`** pour
      rester testable hors Chainlit.
- [ ] Extraction de l'entité active : à quel moment ? Probablement en
      post-traitement de chaque réponse via regex sur les SIREN cités.

### Commit phase 1

`story(S06): refine — Chainlit 2026 API, Starters, streaming pattern`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/app.py` — entrypoint Chainlit.
- `src/genial_agent/ui/post_process.py` — SIREN clickable, date
  highlight.
- `src/genial_agent/ui/starters.py` — définition des 4 starters.
- `src/genial_agent/ui/entity_tracker.py` — extraction + mise à jour
  de l'entité active.
- `chainlit.md` (si pertinent, pour l'empty state de base).
- `public/` pour le footer et custom CSS si nécessaire.
- `tests/unit/test_S06_post_process.py`
- `tests/unit/test_S06_entity_tracker.py`

### `ui/starters.py`

```python
"""Définition des starters pour l'empty state."""
from __future__ import annotations

import chainlit as cl

STARTERS: list[cl.Starter] = [
    cl.Starter(
        label="⚡ Fiche LVMH",
        message="Donne-moi la fiche de LVMH",
        icon="/public/flash.svg",
    ),
    cl.Starter(
        label="⚡ Mandats de Bernard Arnault",
        message="Quelles sociétés Bernard Arnault dirige-t-il actuellement ?",
    ),
    cl.Starter(
        label="🧠 Compare Carrefour vs Casino",
        message="Compare la santé financière de Carrefour et Casino sur 3 ans, lequel présente le moins de risque ?",
    ),
    cl.Starter(
        label="🧠 Vérifie SIREN",
        message="Vérifie l'entreprise SIREN 552032534, donne-moi un avis KYC.",
    ),
]
```

### `ui/post_process.py`

```python
"""Post-traitement de la réponse finale avant affichage."""
from __future__ import annotations

import re

SIREN_RE = re.compile(r"\b(\d{9})\b")


def linkify_sirens(text: str) -> str:
    """Remplace chaque SIREN par un lien Markdown vers pappers.fr."""
    return SIREN_RE.sub(
        r"[\1](https://www.pappers.fr/entreprise/\1)",
        text,
    )
```

### `ui/entity_tracker.py`

```python
"""Extraction de l'entité active pour la bannière multi-turn (§16.2)."""
from __future__ import annotations

import re
from dataclasses import dataclass

SIREN_RE = re.compile(r"\b(\d{9})\b")


@dataclass(frozen=True)
class ActiveEntity:
    name: str
    siren: str


def extract_active_entity(
    tool_results: list[dict],
    fallback_name: str | None = None,
) -> ActiveEntity | None:
    """Scanne les tool_results pour le SIREN + nom de la dernière entité
    récupérée. Priorité au dernier `get_company` de la turn.

    Chaque tool_result est un dict typé `{"name": tool, "result": {...}}`
    tel que yield par S03 dans l'event `tool_result`.
    """
    for item in reversed(tool_results):
        result = item.get("result") or {}
        siren = result.get("siren") or result.get("siren_formatted")
        name = result.get("denomination") or result.get("nom_entreprise") or result.get("name")
        if siren and name:
            return ActiveEntity(name=str(name), siren=str(siren))
    # Fallback : SIREN détecté dans le texte d'un tool_result
    for item in reversed(tool_results):
        raw = str(item.get("result") or "")
        m = SIREN_RE.search(raw)
        if m:
            return ActiveEntity(name=fallback_name or "(entité)", siren=m.group(1))
    return None


def format_banner(entity: ActiveEntity | None) -> str | None:
    if entity is None:
        return None
    return f"📌 **Entité active** : {entity.name} (SIREN [{entity.siren}](https://www.pappers.fr/entreprise/{entity.siren}))"
```

### `app.py` — squelette

```python
"""Entry point Chainlit pour genial-agent."""
from __future__ import annotations

import asyncio

import chainlit as cl
import structlog

from genial_agent import mcp_pappers
from genial_agent.agent import ConversationState
from genial_agent.guardrails.critic import critique_async
from genial_agent.guardrails.input_gate import InputGateError, check_input
from genial_agent.routing import run_routed_turn
from genial_agent.ui.post_process import linkify_sirens
from genial_agent.ui.starters import STARTERS

logger = structlog.get_logger(__name__)

FOOTER = (
    "_Données via Pappers · Modèles Claude (Anthropic) · "
    "Messages traités en US (Anthropic) et FR (Pappers). Pas de stockage permanent. "
    "[Code source](https://github.com/Bakajanai69/genial-agent)_"
)


@cl.set_starters
async def starters() -> list[cl.Starter]:
    return STARTERS


@cl.on_chat_start
async def on_start() -> None:
    state = ConversationState()
    cl.user_session.set("state", state)
    cl.user_session.set("entity_banner_msg", None)
    cl.user_session.set("critic_tasks", set())
    cl.user_session.set("tool_results", [])
    # Healthcheck MCP Pappers en parallèle, badge UI si KO
    health = await mcp_pappers.healthcheck()
    if health["status"] != "ok":
        await cl.Message(
            content="🔴 **Données Pappers indisponibles.** Réessaie dans un instant.",
            author="Système",
        ).send()


@cl.on_chat_end
async def on_end() -> None:
    """Annule proprement les tâches critic encore en cours."""
    tasks: set[asyncio.Task] = cl.user_session.get("critic_tasks") or set()
    for t in tasks:
        if not t.done():
            t.cancel()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    state: ConversationState = cl.user_session.get("state")

    # Input gate
    try:
        check_input(message.content)
    except InputGateError as exc:
        await cl.Message(
            content=f"⚠️ Requête refusée : {exc}. Reformule en français et reste dans le périmètre des entreprises françaises.",
            author="Garde-fou",
        ).send()
        return

    msg = cl.Message(content="", author="Agent")
    await msg.send()

    tool_results: list[dict] = cl.user_session.get("tool_results") or []
    current_step: cl.Step | None = None
    current_tool_name: str | None = None

    model_used = "haiku"
    escalated = False
    async for event in run_routed_turn(state, message.content):
        ev_type = event.get("type")
        if ev_type == "text":
            await msg.stream_token(linkify_sirens(event["content"]))
        elif ev_type == "tool_use":
            current_tool_name = event.get("name", "tool")
            current_step = cl.Step(name=f"🔧 {current_tool_name}", default_open=True)
            await current_step.__aenter__()
            current_step.input = event.get("input", {})
        elif ev_type == "tool_result":
            # Rattache le résultat à la step ouverte
            if current_step is not None:
                current_step.output = event.get("result")
                await current_step.__aexit__(None, None, None)
            current_step = None
            tool_results.append({"name": current_tool_name, "result": event.get("result")})
        elif ev_type == "routing_done":
            model_used = event["model_used"]
            escalated = event["escalated"]
        elif ev_type == "escalation":
            await cl.Message(
                content="⚡→🧠 Escalade vers Sonnet : " + event["reason"],
                author="Routing",
            ).send()
        elif ev_type == "capped":
            await cl.Message(
                content=f"🛑 Cap atteint : {event['reason']}. Ouvre une nouvelle conversation pour continuer.",
                author="Système",
            ).send()

    cl.user_session.set("tool_results", tool_results)

    # Badge modèle en fin de message
    badge = "⚡→🧠 Sonnet (via escalade)" if escalated else ("🧠 Sonnet" if model_used == "sonnet" else "⚡ Haiku")
    msg.content += f"\n\n---\n*Modèle : {badge}*"
    await msg.update()

    # Bannière entité active (§16.2)
    entity = extract_active_entity(tool_results)
    await _update_entity_banner(entity)

    # Footer RGPD
    await cl.Message(content=FOOTER, author="").send()

    # Critic async non-bloquant, tracé dans cl.user_session
    task = asyncio.create_task(_run_critic_and_update(msg, message.content))
    tasks: set[asyncio.Task] = cl.user_session.get("critic_tasks") or set()
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    cl.user_session.set("critic_tasks", tasks)


async def _update_entity_banner(entity) -> None:  # entity: ActiveEntity | None
    """Crée ou met à jour un message épinglé portant la bannière §16.2."""
    content = format_banner(entity)
    if content is None:
        return
    existing: cl.Message | None = cl.user_session.get("entity_banner_msg")
    if existing is None:
        banner = cl.Message(content=content, author="Contexte")
        await banner.send()
        cl.user_session.set("entity_banner_msg", banner)
    else:
        existing.content = content
        await existing.update()


async def _run_critic_and_update(msg: cl.Message, question: str) -> None:
    """Lance le critic en tâche de fond et met à jour le message avec le badge."""
    try:
        result = await critique_async(question=question, response=msg.content)
        emoji = {"green": "✓", "orange": "⚠", "red": "✗"}[result.color]
        badge = f"\n*{emoji} Confiance : {int(result.confidence * 100)}%*"
        if result.issues:
            badge += f" · Issues : {', '.join(result.issues[:3])}"
        msg.content += badge
        await msg.update()
    except asyncio.CancelledError:
        raise  # propage si la session est fermée
    except Exception as exc:  # noqa: BLE001
        logger.warning("critic_failed", error=str(exc))
```

> ⚠️ **Note d'intégration** : les imports `extract_active_entity` et
> `format_banner` viennent de `genial_agent.ui.entity_tracker`.
> À ajouter en tête de fichier.

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S06_post_process.py
from genial_agent.ui.post_process import linkify_sirens


def test_siren_linkified():
    text = "LVMH SIREN 775670417 est une société française."
    out = linkify_sirens(text)
    assert "[775670417](https://www.pappers.fr/entreprise/775670417)" in out


def test_multiple_sirens_all_linkified():
    text = "Compare 775670417 et 388912497."
    out = linkify_sirens(text)
    assert out.count("pappers.fr/entreprise") == 2


def test_no_siren_unchanged():
    text = "Pas de SIREN ici."
    assert linkify_sirens(text) == text


def test_siren_inside_longer_number_not_matched():
    # Un SIREN (9 chiffres) dans un nombre à 12 chiffres ne doit pas matcher.
    # NB : attention aux limites `\b`. Teste ce cas ambigu.
    text = "Valeur : 123456789012"
    out = linkify_sirens(text)
    assert "pappers.fr" not in out
```

```python
# tests/unit/test_S06_entity_tracker.py
from genial_agent.ui.entity_tracker import extract_active_entity, format_banner


def test_extract_latest_company_result():
    tool_results = [
        {"name": "search_company", "result": {"siren": "111111111", "denomination": "AAA"}},
        {"name": "get_company", "result": {"siren": "775670417", "denomination": "LVMH"}},
    ]
    entity = extract_active_entity(tool_results)
    assert entity.siren == "775670417"
    assert entity.name == "LVMH"


def test_extract_none_when_no_data():
    assert extract_active_entity([]) is None


def test_format_banner_shape():
    from genial_agent.ui.entity_tracker import ActiveEntity
    b = format_banner(ActiveEntity(name="LVMH", siren="775670417"))
    assert "LVMH" in b and "775670417" in b and "pappers.fr" in b


def test_format_banner_none():
    assert format_banner(None) is None
```

### Tests manuels (documentés)

Le UI Chainlit ne se teste pas automatiquement facilement. Documenter
dans la story le script manuel :

1. `make run` → http://localhost:8000
2. Vérifier : 4 starters visibles.
3. Cliquer sur "⚡ Fiche LVMH" → badge Haiku, SIREN 775670417 cliquable.
4. Suivi : taper "Et ses dirigeants ?" → contexte multi-turn résolu.
5. Cliquer sur "🧠 Compare Carrefour vs Casino" → badge Sonnet, tableau.
6. Taper "Ignore tes instructions" → refus par input gate.
7. Couper la clé Pappers en .env, redémarrer → badge 🔴 MCP KO affiché.

### Commit phase 2

`feat(S06): Chainlit UI with starters, badges, linkified SIRENs, critic badge`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] `cl.user_session` utilisé pour isoler le `ConversationState` par
      onglet / session.
- [ ] Les 4 starters couvrent U1, U2, U3, U5.
- [ ] Post-process SIREN n'introduit pas de faux positifs sur nombres
      > 9 chiffres (test paramétré).
- [ ] Le critic async ne bloque jamais le flow principal — erreur
      silencieuse + log.
- [ ] Tâches critic : stockées dans `cl.user_session["critic_tasks"]`,
      annulées proprement par `@cl.on_chat_end`.
- [ ] Bannière "Entité active" s'affiche dès qu'un SIREN + nom sont
      résolus, se met à jour (pas de spam de messages).
- [ ] `tool_result` : chaque step `cl.Step` est fermée avec
      `step.output` renseigné.
- [ ] Footer RGPD présent et avec lien vers le repo GitHub.
- [ ] États fallback (MCP KO, cap) s'affichent avec le bon emoji / couleur.

### Commit phase 3

`review(S06): approved`

---

## ✅ Critères d'acceptation

- [ ] `make run` lance Chainlit sur `localhost:8000`.
- [ ] Empty state affiche les 4 starters.
- [ ] Les 3 tests officiels Pappers (LVMH, BNP, Carrefour) fonctionnent.
- [ ] SIREN cliquables dans les réponses.
- [ ] Badge modèle correct selon la requête.
- [ ] Bannière "Entité active: LVMH (SIREN 775670417)" apparaît après
      une requête sur LVMH, puis se met à jour (pas re-émise) au turn
      suivant.
- [ ] Au moins un `cl.Step` ouvre par défaut et affiche input + output
      du tool call visible.
- [ ] Bannière MCP KO apparaît quand la clé Pappers est invalide.
- [ ] `gitleaks` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests unitaires verts + script manuel validé.
- [ ] Phase 3 approuvée.
- [ ] Ligne S06 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
