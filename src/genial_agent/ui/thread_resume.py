"""Reconstruction d'un ``ConversationState`` depuis les ``steps``
stockés en SQLite par le data layer Chainlit.

Utilisé par ``app.py:on_chat_resume`` quand un utilisateur rouvre une
conversation depuis la sidebar — sans cette reconstruction, l'agent
reprend "from scratch" et ne se souvient pas du contexte des tours
précédents (cross-session multi-turn cassé).

Stratégie defensive
-------------------

Le format Anthropic ``MessageParam`` est strict :

- alternance user/assistant obligatoire,
- 1er message doit être de role ``"user"``,
- ``tool_use`` blocks doivent matcher des ``tool_result`` blocks dans
  le même bloc.

Les ``steps`` Chainlit ne stockent pas les ``tool_use`` Anthropic
structurés (juste l'output texte des outils en step ``"tool"``). Plutôt
que de tenter de reconstruire les paires tool_use ↔ tool_result depuis
les step textes, on **skip** strictement tous les steps non-message
(``tool``, ``run``, ``system_message``, etc.). Conséquence :

- L'agent voit l'historique conversationnel **texte** (user/assistant
  finaux) — suffisant pour résoudre des pronoms et comprendre des
  références ("et son CA ?").
- Il ne voit **pas** les tool_results bruts Pappers — il devra
  re-interroger Pappers s'il a besoin de la donnée structurée.
  C'est OK : le 2e tour reprend depuis 0 côté tool calls,
  l'historique sert juste de contexte conversationnel.

Cap dur sur le nombre de messages reconstruits pour éviter de saturer
le ``MAX_TOKENS_PER_SESSION`` Anthropic (200 K) au 1er nouveau tour
sur une conversation très longue.

Tout est wrap dans un ``try/except`` côté ``on_chat_resume`` — en cas
d'exception inattendue, on retombe sur ``ConversationState()`` neuf.
"""

from __future__ import annotations

from typing import Any, cast

import structlog
from anthropic.types import MessageParam

from genial_agent.agent import ConversationState, wrap_user_input

logger = structlog.get_logger(__name__)


MAX_RESUMED_MESSAGES = 20
"""Nombre max de messages texte reconstruits depuis les steps
historiques. 20 messages ≈ 30-50 K tokens dans le pire cas, bien sous
le cap ``MAX_TOKENS_PER_SESSION = 200_000``."""


_USER_STEP_TYPE = "user_message"
"""Type Chainlit pour un message envoyé par l'utilisateur."""


_ASSISTANT_STEP_TYPES: frozenset[str] = frozenset({"assistant_message"})
"""Types Chainlit considérés comme un message final de l'assistant.

Les steps ``"tool"``, ``"run"``, ``"system_message"`` etc. sont
**exclus** : pas de contenu texte conversationnel à exposer comme
``role="assistant"`` côté Anthropic."""


def _is_user_step(step: dict[str, Any]) -> bool:
    return step.get("type") == _USER_STEP_TYPE


def _is_assistant_step(step: dict[str, Any]) -> bool:
    return step.get("type") in _ASSISTANT_STEP_TYPES


def _step_text_content(step: dict[str, Any]) -> str:
    """Extrait le texte brut d'un step. Retourne ``""`` si l'output
    n'est pas une string ou est vide après strip."""
    output = step.get("output")
    if isinstance(output, str):
        return output.strip()
    return ""


def _filter_alternating(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Garde uniquement les steps user/assistant **avec output texte
    non-vide** en alternance stricte démarrant par un user.

    On pré-filtre les steps :

    1. Type non-message (``tool``, ``run``, ``system_message``…) → drop.
    2. Output vide ou non-string → drop (n'aurait rien à mettre dans
       ``state.messages.content``).

    Ensuite on applique l'alternance stricte :

    - 1er message doit être ``role="user"`` (Anthropic constraint) →
      les assistant orphelins en tête sont droppés.
    - Deux steps consécutifs du même role → on garde le 1er.
    """
    # Pré-filtre : type message ET output texte non-vide.
    eligible = [
        s for s in steps if (_is_user_step(s) or _is_assistant_step(s)) and _step_text_content(s)
    ]

    paired: list[dict[str, Any]] = []
    for step in eligible:
        if not paired:
            # 1er message : doit être user.
            if _is_user_step(step):
                paired.append(step)
            continue
        last_was_user = _is_user_step(paired[-1])
        if last_was_user and _is_assistant_step(step) or not last_was_user and _is_user_step(step):
            paired.append(step)
        # Sinon : skip (consécutifs même role, on garde le 1er).
    return paired


def _cap_messages(paired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cap à ``MAX_RESUMED_MESSAGES`` derniers en s'assurant que ça
    commence toujours par un user (Anthropic constraint)."""
    if len(paired) > MAX_RESUMED_MESSAGES:
        paired = paired[-MAX_RESUMED_MESSAGES:]
    while paired and not _is_user_step(paired[0]):
        paired.pop(0)
    return paired


def reconstruct_state_from_steps(
    steps: list[Any] | None,
) -> ConversationState:
    """Reconstruit un ``ConversationState`` depuis une liste de steps
    Chainlit (``ThreadDict["steps"]``).

    Garanties :

    - Retourne toujours un ``ConversationState`` valide (jamais
      ``None``, jamais d'exception remontée — log + fallback).
    - ``state.messages`` respecte l'alternance user/assistant et démarre
      par un user (sinon vide).
    - Pas plus de ``MAX_RESUMED_MESSAGES`` messages reconstruits.

    Si la liste est vide, ``None``, ou si tous les steps sont
    malformés, retourne un ``ConversationState()`` neuf.
    """
    state = ConversationState()
    if not steps or not isinstance(steps, list):
        return state

    # Filtrer les dicts valides (defense contre des entries None /
    # str / autres types qui pourraient être stockées par erreur).
    dict_steps: list[dict[str, Any]] = [s for s in steps if isinstance(s, dict)]
    if not dict_steps:
        return state

    # Tri par created_at au cas où le data layer SQLite renverrait
    # dans un autre ordre (le ORDER BY est déjà fait côté query, mais
    # défense en profondeur ici).
    dict_steps.sort(key=lambda s: s.get("createdAt") or s.get("created_at") or "")

    paired = _filter_alternating(dict_steps)
    paired = _cap_messages(paired)

    for step in paired:
        text = _step_text_content(step)
        if not text:
            continue
        message: MessageParam
        if _is_user_step(step):
            message = cast(
                MessageParam,
                {"role": "user", "content": wrap_user_input(text)},
            )
        else:
            message = cast(
                MessageParam,
                {"role": "assistant", "content": text},
            )
        state.messages.append(message)

    # S'il reste un assistant en queue après filtrage texte vide, on
    # le retire pour ne pas finir l'historique sur un assistant — le
    # prochain message envoyé sera user, donc OK quand même côté
    # Anthropic, mais plus propre de finir sur un assistant complet.
    return state
