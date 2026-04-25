"""Entry point Chainlit pour genial-agent (cahier §16).

Le pipeline (input gate, routing Haiku/Sonnet, agent loop, validator,
critic) est délégué intégralement à ``guardrails.run_guarded_turn`` (S05).
S06 ne fait que :

1. Instancier ``ConversationState`` par session Chainlit.
2. Lancer un healthcheck Pappers au boot d'un chat (visible si KO).
3. Streamer les events de ``run_guarded_turn`` vers la UI.
4. Maintenir une bannière "Entité active" entre les turns.
5. Appliquer les post-traitements UI (linkify SIREN, badge modèle).

**Décision architecture** (cf. README §"Décisions de cohérence" §5) :
S06 crée l'unique ``@cl.on_chat_start`` du projet. S10 (stretch vocal)
l'étend par fonction call (``await voice.on_chat_start_extras()``)
plutôt que par redéfinition du décorateur.
"""

from __future__ import annotations

import chainlit as cl
import structlog

from genial_agent import mcp_pappers
from genial_agent.agent import ConversationState
from genial_agent.guardrails import budget, run_guarded_turn
from genial_agent.ui.entity_tracker import (
    ActiveEntity,
    extract_active_entity,
    format_banner,
)
from genial_agent.ui.events import TurnState, dispatch_event
from genial_agent.ui.post_process import linkify_sirens, model_badge
from genial_agent.ui.starters import STARTERS

logger = structlog.get_logger(__name__)


@cl.set_starters
async def starters() -> list[cl.Starter]:
    """Empty state : 4 starters cliquables (cf. cahier §16.1)."""
    return STARTERS


@cl.on_chat_start
async def on_chat_start() -> None:
    """Création du state par session + healthcheck MCP visible.

    Une session = un ``ConversationState`` neuf (pas de partage
    cross-session, cf. cahier §17.4 concurrence). La bannière "Entité
    active" est initialisée à ``None`` et remplacée à la fin du 1er
    turn qui résout une entité.
    """
    cl.user_session.set("state", ConversationState())
    cl.user_session.set("entity_banner_msg", None)

    health = await mcp_pappers.healthcheck()
    if health["status"] != "ok":
        logger.warning(
            "ui_pappers_health_ko",
            latency_ms=health.get("latency_ms"),
            error=health.get("error"),
        )
        await cl.Message(
            content=(
                f"🔴 **Données Pappers temporairement indisponibles** "
                f"(latency {health.get('latency_ms', '?')}ms). "
                f"Réessaie dans un instant — l'agent va répondre, mais "
                f"sans accès aux données entreprise."
            ),
            author="Système",
            type="system_message",
        ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    """Pour chaque message utilisateur : drain ``run_guarded_turn``,
    rendre les events au fil de l'eau, post-traiter et mettre à jour
    la bannière entité.
    """
    state: ConversationState = cl.user_session.get("state") or ConversationState()
    session_id: str = cl.user_session.get("id") or "unknown"

    # Bulle agent vide, sera remplie par stream_token / update.
    msg = cl.Message(content="", author="Agent")
    await msg.send()

    turn_state = TurnState(msg=msg)

    turn_gen = run_guarded_turn(state, message.content, session_id)
    try:
        async for event in turn_gen:
            await dispatch_event(event, turn_state)
    finally:
        # PEP 789 + S03 invariant I5 : libère ``state.lock`` même si un
        # dispatch lève (typo dans events.py, run_guarded_turn cancellé
        # par le client, etc.). Sans ``aclose()``, un onglet fermé
        # pendant un stream peut laisser le lock détenu et bloquer le
        # prochain ``run_turn`` de la même session.
        await turn_gen.aclose()

    # Si le pipeline a refusé l'input (C1), la bulle agent a déjà été
    # supprimée par le dispatcher → on n'ajoute rien (ni badge, ni
    # bannière entité — il n'y a pas eu de tool call à scanner).
    if turn_state.input_rejected:
        return

    # Final pass linkify SIREN si ``validator_degraded`` n'a pas tourné
    # (réponse jugée propre par C5 → on linkifie nous-mêmes).
    if not turn_state.final_text:
        turn_state.final_text = linkify_sirens(msg.content or "")
        msg.content = turn_state.final_text
        await msg.update()

    # Badge modèle final + sub-line confiance critic (déjà ajouté par
    # ``critic_result`` event si le critic a tourné). Le badge modèle
    # est appendé à ``msg.content`` après le critic — ainsi il survit
    # au cas où le critic mute ``msg.content`` indépendamment.
    badge = model_badge(
        model_used=turn_state.model_used,
        escalated=turn_state.escalated,
        escalation_mode=turn_state.escalation_mode,
    )
    msg.content = (msg.content or "") + f"\n\n---\n*Modèle : {badge}*"
    turn_state.final_text = msg.content
    await msg.update()

    # Bannière entité active (idempotente, no-op si entité inchangée).
    entity = extract_active_entity(turn_state.tracker)
    await _update_entity_banner(entity)


@cl.on_chat_end
async def on_chat_end() -> None:
    """Cleanup à la fermeture du chat.

    Le pipeline gère ses propres tasks (critic, gen close) : on n'a
    rien à annuler. On libère uniquement le slot de token budget pour
    cette session pour éviter que le ``defaultdict`` interne ne grossisse
    indéfiniment sur un serveur long-lived.
    """
    session_id = cl.user_session.get("id")
    if session_id:
        await budget.reset(session_id)


async def _update_entity_banner(entity: ActiveEntity | None) -> None:
    """Crée ou met à jour le ``cl.Message`` épinglé portant la bannière
    §16.2.

    Politique :

    - Aucune entité résolue ce turn → no-op (la bannière précédente, si
      elle existe, reste affichée — pertinent pour un follow-up ``"et
      ses dirigeants ?"``).
    - 1ère entité résolue → ``cl.Message(...).send()`` + stockage en
      ``cl.user_session``.
    - Même contenu de bannière → no-op (évite un re-update inutile qui
      remonterait la bannière dans le flux WebSocket Chainlit).
    - Nouvelle entité différente → ``existing.content = new`` +
      ``await existing.update()``.
    """
    content = format_banner(entity)
    if content is None:
        return
    existing: cl.Message | None = cl.user_session.get("entity_banner_msg")
    if existing is None:
        banner = cl.Message(content=content, author="Contexte", type="system_message")
        await banner.send()
        cl.user_session.set("entity_banner_msg", banner)
        return
    if existing.content == content:
        return
    existing.content = content
    await existing.update()
