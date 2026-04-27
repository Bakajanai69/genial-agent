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
import re as _re_module
import uuid

import chainlit as cl
import structlog

# S09.7 hotfix ordre imports : bootstrap DOIT précéder l'import de
# ``mcp_pappers`` (qui charge ``mcp_cache.cache`` au module-load et
# tente de lire ``MCP_CACHE_PERSIST_PATH``). Sans ça, le cache se
# charge avant que le bootstrap ait copié le bake → ``/data/...``
# → file_absent → cache in-memory vide jusqu'au prochain redémarrage.
# Validé live sur le déploiement 826c6c9d (logs : mcp_cache_load_skip
# avant data_bootstrap_copy).
from genial_agent.data_bootstrap import bootstrap_volume_from_bake

bootstrap_volume_from_bake()

from genial_agent import mcp_pappers  # noqa: E402
from genial_agent.agent import ConversationState  # noqa: E402
from genial_agent.auth.mount import mount_auth_middleware  # noqa: E402
from genial_agent.config import settings  # noqa: E402
from genial_agent.guardrails import budget, run_guarded_turn  # noqa: E402
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP  # noqa: E402
from genial_agent.observability import (  # noqa: E402
    cache as idempotence_cache,
)
from genial_agent.observability import (  # noqa: E402
    configure_logging,
    mount_routes,
)
from genial_agent.observability import (  # noqa: E402
    degraded as credits_degraded,
)
from genial_agent.observability import (  # noqa: E402
    incr as stats_incr,
)
from genial_agent.observability import (  # noqa: E402
    remaining as credits_remaining,
)
from genial_agent.ui.chainlit_data_layer import (  # noqa: E402
    SESSION_OWNER_KEY,
    AnonymousSQLiteDataLayer,
)
from genial_agent.ui.entity_tracker import (  # noqa: E402
    ActiveEntity,
    extract_active_entity,
    format_banner,
)
from genial_agent.ui.events import TurnState, dispatch_event  # noqa: E402
from genial_agent.ui.post_process import linkify_sirens, model_badge  # noqa: E402
from genial_agent.ui.starters import STARTERS  # noqa: E402
from genial_agent.voice.mount import mount_voice_routes  # noqa: E402

# S07 — configurer structlog JSON + monter /health et /stats AVANT que
# Chainlit serve la 1ère requête. Les deux fonctions sont idempotentes
# (flag interne) — ce module est ré-importé en test par TestClient
# sans effet de bord.
configure_logging()
mount_routes()
# Pose ``genial_owner_id`` côté serveur dès la 1re requête HTTP, avant
# que ``header_auth_callback`` ne lise les cookies. Ferme la race
# condition au 1er pageload où le cookie n'était posé que côté JS et
# qui rendait orphelins les threads créés sous le fallback ``anon-*``.
mount_auth_middleware()
# S10 — monter /v1/chat/completions + /voice-meta.html si voice mode
# activé. **No-op si ``settings.ENABLE_VOICE_MODE`` est faux** (route
# absente, surface d'attaque nulle).
mount_voice_routes()

logger = structlog.get_logger(__name__)

# Seuil "crédits bas" : 10 % du cap (cahier §16.3 R16 — bandeau
# orange). En-deçà, l'UI affiche un avertissement non-bloquant avant
# de lancer le pipeline.
_CREDITS_LOW_THRESHOLD = max(1, DAILY_PAPPERS_CREDITS_CAP // 10)

# Cap dur sur le healthcheck Pappers au boot d'un chat. Au-delà, on
# bascule en mode "MCP KO" visible plutôt que de faire poireauter
# l'évaluateur sur un on_chat_start qui ne se termine jamais.
_HEALTHCHECK_TIMEOUT_S: float = 3.0

# S09.6 (H3') — Chemin du fichier SQLite du data layer Chainlit. En prod
# Railway, on pointe sur le volume monté (``/data/cl_threads.db`` via
# ``CHAINLIT_DATA_LAYER_DB_PATH``). En local, fallback sur le bake.
import os as _os  # noqa: E402

_CL_DB_PATH = _os.getenv("CHAINLIT_DATA_LAYER_DB_PATH", "data/cl_threads.db")


@cl.data_layer
def get_data_layer() -> AnonymousSQLiteDataLayer:
    """Custom Chainlit data layer SQLite anonymous-user.

    Persistance des threads + steps pour que la sidebar Chainlit (liste
    des conversations précédentes) survive aux redémarrages serveur.
    Pas d'auth, pas de S3, pas de Postgres — un seul fichier SQLite
    baké dans Docker + monté sur le volume Railway au runtime.
    """
    return AnonymousSQLiteDataLayer(db_path=_CL_DB_PATH)


# S09.7 hotfix : Chainlit n'invoque ``data_layer.list_threads`` (et
# donc n'affiche pas la sidebar des conversations passées) **que si un
# ``cl.User`` est défini**. Sans ``header_auth_callback`` /
# ``password_auth_callback``, l'user reste None et la sidebar est
# inaccessible — c'est pour ça qu'on observait "aucune conversation
# passée" malgré le data layer + volume + cookie en place.
#
# On ajoute donc un ``header_auth_callback`` qui retourne **toujours**
# un ``cl.User`` :
# - identifier = cookie ``genial_owner_id`` posé par
#   ``public/owner-cookie.js`` côté navigateur (durable cross-session
#   cross-refresh, persisté en localStorage)
# - fallback ``anon-<uuid>`` éphémère si le cookie n'est pas encore
#   posé (1er pageload avant que le JS ait tourné).
#
# Aucune authentification réelle (pas de password, pas d'OAuth) — c'est
# uniquement pour activer la sidebar Chainlit avec un identifiant
# persistant côté navigateur. Cohérent avec le mode "anonymous user"
# documenté en S09.6 (data_layer P1-3).
_OWNER_COOKIE_RE_AUTH = _re_module.compile(r"\bgenial_owner_id=([A-Za-z0-9-]{8,64})")


@cl.header_auth_callback
async def auth_callback(headers: object) -> cl.User | None:
    """Authentifie le visiteur via le cookie ``genial_owner_id``.

    Toujours retourne un User (jamais None) pour que Chainlit active
    la sidebar conversations. L'identifier est l'UUID du cookie quand
    présent, sinon un UUID éphémère ``anon-<hex16>``.

    **Async** parce que Chainlit ``header_auth_callback`` attend
    ``Awaitable[Optional[User]]`` (signature documentée
    ``async def header_auth_callback(headers: Headers)``).
    """
    cookie_header = headers.get("cookie", "") if hasattr(headers, "get") else ""
    match = _OWNER_COOKIE_RE_AUTH.search(cookie_header) if cookie_header else None
    if match:
        logger.info("auth_user_resolved_from_cookie", identifier_prefix=match.group(1)[:8])
        return cl.User(
            identifier=match.group(1),
            metadata={"source": "cookie", "persistent": True},
        )
    # Fallback éphémère : 1er pageload avant que owner-cookie.js ait tourné.
    # Le 2ème pageload récupèrera le cookie et l'identifier sera stable.
    fallback = f"anon-{uuid.uuid4().hex[:16]}"
    logger.info("auth_user_fallback_anon", identifier_prefix=fallback[:8])
    return cl.User(
        identifier=fallback,
        metadata={"source": "fallback", "persistent": False},
    )


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
    # Flag de dédup du bandeau crédits bas (review M2). Reset à chaque
    # ouverture de chat pour qu'une nouvelle session puisse re-voir
    # l'avertissement même si l'utilisateur a déjà été notifié dans une
    # session précédente.
    cl.user_session.set("credits_low_banner_shown", False)
    # Review S09.6 P1-3 : owner_id par session pour isoler les threads
    # de la sidebar Chainlit. Sans cookie persistant, l'UUID change à
    # chaque rafraîchissement page — la sidebar redevient vide pour ce
    # visiteur, mais reste invisible aux autres.
    if not cl.user_session.get(SESSION_OWNER_KEY):
        cl.user_session.set(SESSION_OWNER_KEY, uuid.uuid4().hex)

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

    # S09.6 (E2) — Pre-warm best-effort du cache MCP au boot d'un chat.
    # Couvre le cas où le bake Docker / le volume Railway ne contient
    # pas (encore) les sirenisateurs des entités golden — ex. premier
    # déploiement avec un cache vide. N'échoue jamais : si crédits
    # épuisés ou MCP KO, on log et on continue. Mode dégradé skippé
    # (pas la peine de tenter des appels qui vont être refusés).
    if settings.PAPPERS_API_KEY and not credits_degraded():
        try:
            await mcp_pappers.prewarm_cache()
        except Exception as exc:  # noqa: BLE001 — best-effort, pas un bloquant
            logger.info(
                "ui_prewarm_skip_at_boot",
                error_type=type(exc).__name__,
            )


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

        # 1bis. Compteur tour utilisateur (review B1) — incrémenté UNE
        # fois par message effectivement engagé dans le pipeline. Cache
        # hit idempotence (return ci-dessus) n'est pas compté car aucun
        # crédit / appel LLM n'est consommé.
        stats_incr(total_turns=1)

        # 2. Bandeau crédits bas (cahier §16.3 R16) — non-bloquant.
        # Affiché **une seule fois par session** (review M2) : sinon
        # l'utilisateur reçoit le bandeau à chaque message une fois le
        # seuil franchi, bruit visuel pour rien.
        rem = credits_remaining()
        already_warned = bool(cl.user_session.get("credits_low_banner_shown"))
        if rem < _CREDITS_LOW_THRESHOLD and not already_warned:
            logger.warning("ui_credits_low_banner", remaining=rem)
            cl.user_session.set("credits_low_banner_shown", True)
            # Wording corrigé (review M1) : la zone "bas" est un
            # **avertissement**, le mode cache-only effectif ne
            # s'enclenche qu'à ``remaining == 0`` via ``credit_guard.degraded()``.
            await cl.Message(
                content=(
                    f"⚠ **Budget Pappers bas** — il reste {rem} appels sur "
                    f"{DAILY_PAPPERS_CREDITS_CAP} aujourd'hui. Au-delà, "
                    f"l'agent basculera en mode cache-only sur les entités "
                    f"connues (LVMH, BNP, Carrefour)."
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

        # S09.7 UI : le rewrappage réflexion/réponse est désormais
        # appliqué dans le dispatch de l'event ``routing_done`` (cf.
        # ``ui/events.py``), avant que validator_degraded ou linkify
        # ne touchent au msg. Pas de post-processing ici.

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


# S09.7 Axe 3 C4 — Continuation prompts injectés dans le pipeline
# quand l'utilisateur clique sur les actions « Continuer » / « Synthèse
# partielle ». Pas de logique métier : on délègue intégralement au LLM
# via une instruction texte. Le ``ConversationState`` session-scoped
# (vault inclus) est préservé donc l'agent reprend là où il s'est
# arrêté.
_CONTINUE_USER_PROMPT = (
    "Continue depuis où tu t'es arrêté. Le contexte (Payload Vault, "
    "tool results précédents) est préservé."
)
_SYNTHESIZE_USER_PROMPT = (
    "Synthétise ce que tu as déjà obtenu jusqu'ici à partir des tool "
    "results disponibles dans le contexte — sans relancer de nouveaux "
    "appels Pappers coûteux. Indique clairement ce qui manque encore "
    "si la réponse est partielle."
)


async def _resume_after_cap(continuation_prompt: str) -> None:
    """Relance ``run_guarded_turn`` sur le même state après un cap.

    Le bouton « Continuer » / « Synthèse » de la UI déclenche cette
    fonction. Le state est récupéré via ``cl.user_session`` (jamais un
    singleton global). Si le budget tokens était saturé, on le reset
    pour la session courante — sinon le pipeline ré-émet immédiatement
    le même cap (boucle infinie UX).
    """
    state: ConversationState = cl.user_session.get("state") or ConversationState()
    session_id = _resolve_session_id()

    # Reset budget pour cette session : sans ça, un cap_token_budget
    # ré-firefires immédiatement. Le user a explicitement demandé à
    # continuer → l'audit trail garde la trace du capped précédent.
    await budget.reset(session_id)

    msg = cl.Message(content="", author="Agent")
    await msg.send()
    turn_state = TurnState(msg=msg)

    turn_gen = run_guarded_turn(state, continuation_prompt, session_id)
    try:
        async for event in turn_gen:
            await dispatch_event(event, turn_state)
    finally:
        await turn_gen.aclose()
        await _drain_orphan_steps(turn_state)

    if turn_state.input_rejected:
        return

    if not turn_state.linkify_applied:
        msg.content = linkify_sirens(msg.content or "")
        turn_state.linkify_applied = True
        await msg.update()

    badge = model_badge(
        model_used=turn_state.model_used,
        escalated=turn_state.escalated,
        escalation_mode=turn_state.escalation_mode,
    )
    msg.content = (msg.content or "") + f"\n\n---\n*Modèle : {badge}*"
    await msg.update()

    entity = extract_active_entity(turn_state.tracker)
    await _update_entity_banner(entity)


@cl.action_callback("continue_turn")
async def on_continue_turn(action: cl.Action) -> None:  # noqa: ARG001
    """Relance le tour avec le même state (vault préservé)."""
    logger.info("ui_cap_continue_clicked")
    await _resume_after_cap(_CONTINUE_USER_PROMPT)


@cl.action_callback("synthesize_partial")
async def on_synthesize_partial(action: cl.Action) -> None:  # noqa: ARG001
    """Demande au LLM une synthèse de l'existant sans nouveau Pappers."""
    logger.info("ui_cap_synthesize_clicked")
    await _resume_after_cap(_SYNTHESIZE_USER_PROMPT)


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
