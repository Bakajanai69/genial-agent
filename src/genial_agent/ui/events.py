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
        linkify_applied: True dès que ``linkify_sirens`` a tourné sur
            ``msg.content`` (via ``validator_degraded`` ou via le
            post-loop ``app.on_message``). Empêche le double-encodage
            Markdown qui surviendrait si on linkifiait deux fois.
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
    linkify_applied: bool = False
    # S09.7 UI — distinction réflexion vs réponse finale.
    # ``text_sections`` accumule les sections de text streamées entre 2
    # tool_use successifs. À la fin du turn, toutes les sections sauf
    # la dernière (= réponse finale du dernier tour LLM avec
    # stop_reason=end_turn) sont wrappées en *italique* discret pour
    # signaler visuellement le raisonnement intermédiaire.
    text_sections: list[str] = field(default_factory=list)
    current_text_buffer: str = ""


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
        # Streaming brut → bubble principal (UX live preservée).
        # En parallèle, on accumule dans ``current_text_buffer`` pour
        # pouvoir post-process à la fin du turn (distinction réflexion
        # vs réponse finale, cf. ``finalize_reasoning_format``).
        chunk = event.get("content", "")
        state.current_text_buffer += chunk
        await state.msg.stream_token(chunk)
        return

    if et == "tool_use":
        # S09.7 UI : un tool_use signale que le text streamé jusque-là
        # était du raisonnement intermédiaire (pas la réponse finale).
        # On flush le buffer dans ``text_sections`` pour traitement
        # post-turn.
        if state.current_text_buffer.strip():
            state.text_sections.append(state.current_text_buffer)
        state.current_text_buffer = ""

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
        # S09.7 Axe 3 C4 — l'event ``capped`` reste informatif. La UX
        # de continuation est portée par ``cap_continuation_proposed``
        # juste après (avec actions cliquables). On garde ici un
        # message court (pas de "ouvre une nouvelle conversation"
        # historique : le contexte n'est plus un dead-end).
        rc = event.get("reason_code", "?")
        reason = event.get("reason", "?")
        await cl.Message(
            content=f"🛑 Cap atteint (`{rc}`) : {reason}.",
            author="Système",
            type="system_message",
        ).send()
        return

    if et == "cap_continuation_proposed":
        # S09.7 Axe 3 C4 — propose des actions UX non-bloquantes plutôt
        # que d'arrêter sec. Le ``ConversationState`` (vault inclus)
        # est préservé côté ``cl.user_session``, donc cliquer
        # « Continuer » relance ``run_guarded_turn`` sur le même state
        # avec une instruction de continuation. « Synthèse partielle »
        # demande à l'agent de résumer ce qu'il a déjà obtenu sans
        # nouveaux tool calls coûteux.
        rc = event.get("reason_code", "?")
        reason = event.get("reason", "?")
        actions = [
            cl.Action(
                name="continue_turn",
                value=str(rc),
                payload={"reason_code": rc, "reason": reason},
                label="🔄 Continuer",
            ),
            cl.Action(
                name="synthesize_partial",
                value="synthesize",
                payload={"reason_code": rc},
                label="📋 Synthèse partielle",
            ),
        ]
        await cl.Message(
            content=(
                f"⚠️ Limite atteinte (`{rc}` : {reason}). Le contexte "
                f"est préservé — clique sur **Continuer** pour relancer "
                f"ou **Synthèse partielle** pour résumer ce qui a déjà "
                f"été obtenu."
            ),
            author="Système",
            type="system_message",
            actions=actions,
        ).send()
        return

    if et == "routing_done":
        # Source de vérité finale pour le badge modèle.
        state.model_used = event.get("model_used", state.model_used)
        state.escalated = bool(event.get("escalated", state.escalated))
        state.escalation_mode = event.get("escalation_mode", state.escalation_mode)

        # S09.7 UI — rewrappage réflexion vs réponse finale APRÈS le
        # streaming complet (routing_done arrive après le dernier text
        # final), AVANT que validator_degraded ou linkify ne touchent
        # au msg.content. À ce point :
        # - state.text_sections contient les raisonnements intermédiaires
        # - state.current_text_buffer contient la réponse finale
        reformatted = _format_msg_with_reasoning_sections(state)
        if reformatted is not None:
            logger.info(
                "ui_reasoning_rewrap_applied",
                sections_count=len(state.text_sections),
                final_chars=len(state.current_text_buffer),
            )
            state.msg.content = reformatted
            await state.msg.update()
        else:
            logger.debug(
                "ui_reasoning_rewrap_skipped",
                sections_count=len(state.text_sections),
                final_chars=len(state.current_text_buffer),
                linkify_applied=state.linkify_applied,
            )
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
        # Set ``linkify_applied`` pour que le post-loop ``app.on_message``
        # ne re-linkifie pas (sinon double-encodage Markdown sur les SIREN
        # déjà entourés de ``[...](...)``).
        degraded = event.get("degraded_text") or ""
        state.final_text = linkify_sirens(degraded)
        state.msg.content = state.final_text
        state.linkify_applied = True
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


def _format_msg_with_reasoning_sections(state: TurnState) -> str | None:
    """Reformate ``state.msg.content`` pour distinguer visuellement
    les sections de raisonnement intermédiaire de la réponse finale.

    Appelé sur l'event ``routing_done`` (juste après que le streaming
    final est terminé, AVANT que validator_degraded ou linkify ne
    touchent au msg).

    Logique S09.7 UI :

    - Pendant le streaming, on accumule chaque section de text dans
      ``state.text_sections`` (flush sur tool_use), et le buffer
      en cours dans ``state.current_text_buffer``.
    - À ``routing_done``, le buffer en cours = la **réponse finale**
      (le dernier tour LLM a produit du text sans appeler de tool).
    - Toutes les sections précédentes = du **raisonnement
      intermédiaire** ("Je vais rechercher...") → wrap en *italique*
      discret pour qu'elles soient visuellement distinctes de la
      réponse principale.
    - Si l'agent n'a pas chaîné de tool (réponse directe sans tool_use),
      ``text_sections`` est vide → on retourne ``None`` (rien à
      reformater).
    """
    if not state.text_sections:
        # Pas de chaînage tool, rien à distinguer.
        return None

    # Le buffer en cours contient la réponse finale (texte streamé
    # après le dernier tool_use, jusqu'à end_turn).
    final_response = state.current_text_buffer.strip()
    # Sections précédentes = raisonnement.
    reasoning_parts = [s.strip() for s in state.text_sections if s.strip()]

    # Format : citation Markdown ``> 💭 ...`` en italique pour le
    # raisonnement, séparée du final par un saut de ligne. Le ``> ``
    # crée un encart visuel discret côté Chainlit (rendering
    # blockquote standard).
    formatted_reasoning = "\n\n".join(f"> 💭 *{section}*" for section in reasoning_parts)
    if final_response:
        return f"{formatted_reasoning}\n\n{final_response}"
    # Pas de réponse finale (ex: cap firefired juste avant) → on
    # affiche au moins le raisonnement pour transparence.
    return formatted_reasoning


# Alias public pour les tests qui importent l'ancien nom.
format_msg_with_reasoning_sections = _format_msg_with_reasoning_sections
