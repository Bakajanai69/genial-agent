"""Dispatcher des events ``run_guarded_turn`` (S05) vers callbacks UI Chainlit.

Découpage : un sous-handler par catégorie d'event, l'appelant
(``app.on_message``) maintient l'état mutable (msg, step_by_id, tracker,
flags routing) et délègue le rendu Chainlit ici.

**Important** : les sous-handlers ne créent **jamais** d'``asyncio.Task``
ou d'objet à durée de vie longue : tout reste local au turn. Les seuls
states cross-turn vivent dans ``cl.user_session`` (bannière "Entité
active", ``ConversationState`` agent).

Forward-compat : un event de type inconnu est silencieusement ignoré.
S07/S10 pourront ajouter de nouveaux types sans casser S06.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import chainlit as cl
import structlog

from genial_agent.ui.entity_tracker import TurnTracker
from genial_agent.ui.post_process import linkify_sirens

logger = structlog.get_logger(__name__)


@dataclass
class TurnState:
    """État mutable accumulé pendant un turn (jeté à la fin).

    Attributes:
        msg: bulle agent principale (créée vide en début de turn).
        step_by_id: dict ``tool_use_id`` → ``cl.Step`` ouvert pour
            rattacher le ``tool_result`` au bon step.
        tracker: collecte des inputs / previews pour ``ActiveEntity``.
        initial_tier: tier renvoyé par ``routing_initial`` (``haiku``
            ou ``sonnet``).
        model_used: source de vérité finale, override par
            ``routing_done``.
        escalated: ``True`` si une escalade a eu lieu pendant le turn.
        escalation_mode: ``"self"`` ou ``"forced"`` (cf. routing_done).
        final_text: texte affiché final (post validator si triggered).
            Reste vide tant que ``validator_degraded`` n'a pas tourné.
        end_reason: motif du dernier event ``end`` (cf. S03 contrat).
        input_rejected: True si le pipeline a refusé l'input (C1).
            Permet à l'appelant de skipper le post-traitement final.
    """

    msg: cl.Message
    step_by_id: dict[str, cl.Step] = field(default_factory=dict)
    tracker: TurnTracker = field(default_factory=TurnTracker)
    initial_tier: str = "haiku"
    model_used: str = "haiku"
    escalated: bool = False
    escalation_mode: str | None = None
    final_text: str = ""
    end_reason: str | None = None
    input_rejected: bool = False


_END_HUMAN_TEXT: dict[str, str] = {
    "rate_limited": "Limite de débit Anthropic atteinte. Réessaie dans quelques secondes.",
    "transport_error": "Connexion à Claude instable. Réessaie.",
    "api_error": "Erreur API Claude. Si ça persiste, vérifie le statut Anthropic.",
}


_CRITIC_EMOJI: dict[str, str] = {
    "green": "✓",
    "orange": "⚠",
    "red": "✗",
}


async def dispatch_event(event: dict[str, Any], state: TurnState) -> None:
    """Route un event du pipeline vers son rendu Chainlit.

    Mutate ``state`` en place. Tous les ``await`` sont sur des appels
    Chainlit (``send`` / ``update`` / ``stream_token`` / ``__aenter__``).
    Aucun appel réseau métier ici — le pipeline gère tout ça.
    """
    et = event.get("type")

    if et == "input_rejected":
        # ``run_turn`` n'a pas été appelé → la bulle agent vide doit être
        # supprimée pour ne pas laisser d'artefact UI.
        await cl.Message(
            content=(
                f"⚠️ Requête refusée par le garde-fou d'entrée "
                f"(`{event.get('reason_code', '?')}`). Reformule en "
                f"français et reste dans le périmètre des entreprises FR."
            ),
            author="Garde-fou",
            type="system_message",
        ).send()
        await state.msg.remove()
        state.input_rejected = True
        state.end_reason = "input_rejected"
        return

    if et == "routing_initial":
        state.initial_tier = event.get("tier", "haiku")
        return

    if et == "text":
        # Streaming brut, linkify final-pass via ``validator_degraded``
        # ou via le ``app.on_message`` après le ``async for``.
        await state.msg.stream_token(event.get("content", ""))
        return

    if et == "tool_use":
        tu_id = event.get("id", "")
        name = event.get("name", "tool")
        tu_input = event.get("input", {}) or {}
        state.tracker.record_tool_use(tu_input)
        step = cl.Step(
            name=name,
            type="tool",
            default_open=True,
            auto_collapse=True,
            show_input="json",
        )
        await step.__aenter__()
        step.input = tu_input
        state.step_by_id[tu_id] = step
        return

    if et == "tool_result":
        tu_id = event.get("tool_use_id", "")
        preview = event.get("content_preview", "") or ""
        state.tracker.record_tool_result(preview)
        step = state.step_by_id.pop(tu_id, None)
        if step is not None:
            step.output = preview
            if event.get("is_error"):
                step.is_error = True
            await step.__aexit__(None, None, None)
        return

    if et == "llm_meta":
        # Stats — S07 instrumentera depuis le pipeline directement
        # (cf. README "Décisions de cohérence" §3). Côté UI : aucun
        # rendu (cahier §16.5 "pas de compteur coût en direct").
        return

    if et == "escalation":
        mode = event.get("mode") or "?"
        reason = event.get("reason") or "?"
        if mode == "self":
            note = f"⚡→🧠 Haiku a demandé Sonnet : {reason}"
        else:
            note = f"⚡→🧠 Cap déclenché ({reason}), bascule sur Sonnet."
        await cl.Message(content=note, author="Routing", type="system_message").send()
        state.escalated = True
        state.escalation_mode = mode
        return

    if et == "capped":
        rc = event.get("reason_code", "?")
        reason = event.get("reason", "?")
        await cl.Message(
            content=(
                f"🛑 Cap atteint (`{rc}`) : {reason}. Ouvre une nouvelle "
                f"conversation pour repartir sur un budget propre."
            ),
            author="Système",
            type="system_message",
        ).send()
        return

    if et == "routing_done":
        # Source de vérité finale pour le badge modèle.
        state.model_used = event.get("model_used", state.model_used)
        state.escalated = bool(event.get("escalated", state.escalated))
        state.escalation_mode = event.get("escalation_mode", state.escalation_mode)
        return

    if et == "end":
        state.end_reason = event.get("reason", "end_turn")
        if state.end_reason in _END_HUMAN_TEXT:
            await cl.Message(
                content=f"⚠️ {_END_HUMAN_TEXT[state.end_reason]}",
                author="Système",
                type="system_message",
            ).send()
        return

    if et == "hallucination_detected":
        # Informatif côté UI. Le ``validator_degraded`` qui suit pose
        # le disclaimer visible. On log seulement.
        logger.info(
            "ui_hallucination_detected",
            reason_code=event.get("reason_code"),
            orphan_sirens=event.get("orphan_sirens"),
        )
        return

    if et == "validator_degraded":
        # Override du contenu du msg principal avec le texte dégradé
        # (= réponse + disclaimers en pied). Linkify final pass ici.
        degraded = event.get("degraded_text") or ""
        state.final_text = linkify_sirens(degraded)
        state.msg.content = state.final_text
        await state.msg.update()
        return

    if et == "critic_pending":
        # Optionnel : un cl.Message system "vérification en cours…".
        # MVP : silencieux pour ne pas spammer l'historique chat.
        return

    if et == "critic_result":
        emoji = _CRITIC_EMOJI.get(event.get("color", ""), "•")
        confidence = int(round((event.get("confidence", 0.0) or 0.0) * 100))
        issues = event.get("issues") or []
        line = f"\n\n*{emoji} Confiance : {confidence}%*"
        if issues:
            # Limite à 3 issues pour éviter de bouffer la bulle.
            line += f" — _{', '.join(str(i) for i in issues[:3])}_"
        # Append au msg principal — le critic est secondaire, il ne
        # mérite pas son propre bubble (cf. cahier §16.2).
        if not state.final_text:
            state.final_text = state.msg.content or ""
        state.msg.content = state.final_text + line
        await state.msg.update()
        return

    # Forward-compat : event inconnu → log debug + ignore. Évite que
    # S07/S10 cassent S06 quand de nouveaux events apparaîtront.
    logger.debug("ui_event_unknown_ignored", event_type=et)
