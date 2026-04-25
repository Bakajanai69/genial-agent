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

import asyncio
import uuid

import chainlit as cl
import structlog

from genial_agent import mcp_pappers
from genial_agent.agent import ConversationState
from genial_agent.guardrails import budget, run_guarded_turn
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
from genial_agent.observability import (
    cache as idempotence_cache,
)
from genial_agent.observability import (
    configure_logging,
    mount_routes,
)
from genial_agent.observability import (
    remaining as credits_remaining,
)
from genial_agent.ui.entity_tracker import (
    ActiveEntity,
    extract_active_entity,
    format_banner,
)
from genial_agent.ui.events import TurnState, dispatch_event
from genial_agent.ui.post_process import linkify_sirens, model_badge
from genial_agent.ui.starters import STARTERS

# S07 — configurer structlog JSON + monter /health et /stats AVANT que
# Chainlit serve la 1ère requête. Les deux fonctions sont idempotentes
# (flag interne) — ce module est ré-importé en test par TestClient
# sans effet de bord.
configure_logging()
mount_routes()

logger = structlog.get_logger(__name__)

# Seuil "crédits bas" : 10 % du cap (cahier §16.3 R16 — bandeau
# orange). En-deçà, l'UI affiche un avertissement non-bloquant avant
# de lancer le pipeline.
_CREDITS_LOW_THRESHOLD = max(1, DAILY_PAPPERS_CREDITS_CAP // 10)

# Cap dur sur le healthcheck Pappers au boot d'un chat. Au-delà, on
# bascule en mode "MCP KO" visible plutôt que de faire poireauter
# l'évaluateur sur un on_chat_start qui ne se termine jamais.
_HEALTHCHECK_TIMEOUT_S: float = 3.0


def _resolve_session_id() -> str:
    """Source de vérité du ``session_id`` côté UI.

    Ordre de priorité :

    1. ``cl.context.session.id`` — l'API publique stable de Chainlit.
    2. ``cl.user_session.get("id")`` — clé interne posée par Chainlit
       sur certaines versions, fallback historique.
    3. ``uuid.uuid4().hex`` mémorisé dans la session — garantit
       l'unicité par session si Chainlit n'expose rien (jamais observé
       sur 2.11.1, mais on refuse de partager un ``"unknown"`` global
       qui mêlerait les budgets / locks de toutes les sessions).
    """
    try:
        ctx_session = cl.context.session
        candidate = getattr(ctx_session, "id", None)
        if isinstance(candidate, str) and candidate:
            return candidate
    except Exception as exc:  # noqa: BLE001 — best-effort, fallback explicite ci-dessous
        logger.debug("ui_session_id_ctx_unavailable", error_type=type(exc).__name__)

    candidate = cl.user_session.get("id")
    if isinstance(candidate, str) and candidate:
        return candidate

    candidate = cl.user_session.get("_session_id_fallback")
    if isinstance(candidate, str) and candidate:
        return candidate
    fallback = uuid.uuid4().hex
    cl.user_session.set("_session_id_fallback", fallback)
    logger.warning("ui_session_id_fallback_uuid", fallback=fallback)
    return fallback


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

    # Healthcheck Pappers borné dur (cf. ``_HEALTHCHECK_TIMEOUT_S``).
    # ``mcp_pappers.healthcheck`` retourne déjà ``status="ko"`` sur
    # exception, mais ne garantit pas un timeout court sur
    # ``list_available_tools`` — on plafonne ici pour ne jamais bloquer
    # le boot d'un chat.
    try:
        health = await asyncio.wait_for(
            mcp_pappers.healthcheck(),
            timeout=_HEALTHCHECK_TIMEOUT_S,
        )
    except TimeoutError:
        logger.warning(
            "ui_pappers_health_timeout",
            timeout_s=_HEALTHCHECK_TIMEOUT_S,
        )
        health = {
            "status": "ko",
            "latency_ms": int(_HEALTHCHECK_TIMEOUT_S * 1000),
            "tools_count": 0,
            "error": "TimeoutError",
        }

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

    S07 ajoute :

    - bind ``session_id`` en ``contextvars`` pour scoper tous les logs
      du turn (request_id sera bind plus tard par ``pipeline.py``) ;
    - check idempotence ``(session_id, sha256(message))`` TTL 60 s avant
      d'engager des crédits Pappers ;
    - bandeau crédits bas si ``remaining < 10 % du cap`` (cahier §16.3) ;
    - store de la réponse finale dans le cache idempotence post-pipeline.
    """
    state: ConversationState = cl.user_session.get("state") or ConversationState()
    session_id: str = _resolve_session_id()

    with structlog.contextvars.bound_contextvars(session_id=session_id):
        # 1. Idempotence — réponse cached < 60 s ?
        cached = await idempotence_cache.get(session_id, message.content)
        if cached is not None:
            logger.info("ui_idempotence_hit")
            await cl.Message(
                content=cached + "\n\n_(réponse servie depuis le cache idempotence)_",
                author="Agent",
            ).send()
            return

        # 2. Bandeau crédits bas (cahier §16.3 R16) — non-bloquant.
        rem = credits_remaining()
        if rem < _CREDITS_LOW_THRESHOLD:
            logger.warning("ui_credits_low_banner", remaining=rem)
            await cl.Message(
                content=(
                    f"⚠ **Budget Pappers dégradé** — il reste {rem} appels "
                    f"sur {DAILY_PAPPERS_CREDITS_CAP} aujourd'hui. "
                    f"Mode cache-only sur les entités connues "
                    f"(LVMH, BNP, Carrefour)."
                ),
                author="Système",
                type="system_message",
            ).send()

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
            # Drain des ``cl.Step`` orphelines : si le pipeline a coupé
            # entre un ``tool_use`` et son ``tool_result``, une step
            # reste ouverte côté UI = spinner infini visible pour
            # l'évaluateur. On force leur sortie de context manager ici.
            await _drain_orphan_steps(turn_state)

        # Si le pipeline a refusé l'input (C1), la bulle agent a déjà été
        # supprimée par le dispatcher → on n'ajoute rien (ni badge, ni
        # bannière entité — il n'y a pas eu de tool call à scanner). On
        # ne stocke **pas** dans l'idempotence non plus : on ne veut pas
        # qu'un input toxique soit servi-cached pendant 60 s.
        if turn_state.input_rejected:
            return

        # Final pass linkify SIREN. ``linkify_applied`` est posé par le
        # dispatcher si ``validator_degraded`` a tourné (déjà linkifié) ;
        # sinon (chemin nominal sans hallucination détectée) on linkifie
        # ici pour garantir le critère d'acceptation "SIREN cliquables".
        if not turn_state.linkify_applied:
            msg.content = linkify_sirens(msg.content or "")
            turn_state.linkify_applied = True
            turn_state.final_text = msg.content
            await msg.update()

        # Badge modèle final + sub-line confiance critic (déjà ajouté par
        # ``critic_result`` event si le critic a tourné).
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

        # 3. Store dans le cache idempotence pour les 60 prochaines secondes.
        if msg.content:
            await idempotence_cache.set(session_id, message.content, msg.content)


async def _drain_orphan_steps(turn_state: TurnState) -> None:
    """Force l'``__aexit__`` de toutes les ``cl.Step`` encore ouvertes.

    Idempotent : on consomme ``step_by_id`` et on tolère qu'``__aexit__``
    lève (Chainlit a parfois fermé le WebSocket avant ce moment) — on
    log et on continue plutôt que de propager l'exception au turn.
    """
    if not turn_state.step_by_id:
        return
    orphans = list(turn_state.step_by_id.items())
    turn_state.step_by_id.clear()
    for tu_id, step in orphans:
        try:
            # Marque la step comme erreur pour signaler visuellement
            # qu'elle n'a pas eu de ``tool_result`` (cap, abort).
            step.is_error = True
            await step.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning(
                "ui_orphan_step_close_failed",
                tool_use_id=tu_id,
                error_type=type(exc).__name__,
            )


@cl.on_chat_end
async def on_chat_end() -> None:
    """Cleanup à la fermeture du chat.

    Le pipeline gère ses propres tasks (critic, gen close) : on n'a
    rien à annuler. On libère uniquement le slot de token budget pour
    cette session pour éviter que le ``defaultdict`` interne ne grossisse
    indéfiniment sur un serveur long-lived.

    On résout le ``session_id`` via le même chemin que ``on_message``
    (``_resolve_session_id``), sinon un fallback UUID posé en cours de
    conversation ne serait jamais nettoyé (mismatch de clé entre
    ``budget.add(session_id)`` et ``budget.reset(session_id)``).
    """
    session_id = _resolve_session_id()
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
