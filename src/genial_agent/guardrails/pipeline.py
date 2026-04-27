"""Pipeline ``run_guarded_turn`` : wrap S04 avec les garde-fous S05.

Entry point **unique** pour S06 (Chainlit). S06 n'appelle que
``run_guarded_turn``, jamais ``run_routed_turn`` ou ``run_turn``
directement — toute la plomberie des garde-fous est ici.

Chaîne (cf. phase 1 S05 §"Décision majeure : pipeline wrapper unique") :

1. **C1 Input gate** — ``evaluate_input`` ; si rejet, yield
   ``input_rejected`` + return (aucun appel Claude en aval).
2. **C4 Pre-turn budget check** — si ``budget.exhausted(session_id)``,
   yield ``capped(reason_code=cap_token_budget)`` + return.
3. **Run routed turn (S04)** — forward tous les events tels quels,
   intercepter ``llm_meta`` pour alimenter le budget et ``tool_result``
   pour agréger ``allowed_sirens``. Si le budget dépasse in-flight,
   yield ``capped`` (sans ``break`` : S04 finit son itération, l'event
   ``end`` clôture proprement).
4. **C5 Output validator** — extraire ``final_text`` (concat des
   ``text`` events forwarded), ``validate_response(final_text,
   allowed_sirens)``. Sur issues → yield ``hallucination_detected`` et
   ``validator_degraded``.
5. **C6 Haiku-critic async (non-bloquant)** — spawn via
   ``asyncio.create_task``. Yield ``critic_pending``, puis
   ``asyncio.wait_for(task, 10 s)`` + yield ``critic_result`` (fallback
   orange sur timeout).

Contrat d'events : superset du contrat S04, sans régression. Cf. S05
phase 1 §"Contrat d'events pipeline".
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog

from genial_agent.agent import ConversationState
from genial_agent.guardrails.critic import CriticResult, critique_async
from genial_agent.guardrails.input_gate import evaluate_input
from genial_agent.guardrails.output_validator import (
    REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE,
    REASON_CODE_HALLUCINATION_ORPHAN_SIRENS,
    degrade,
    validate_response,
)
from genial_agent.guardrails.sirens import extract_sirens
from genial_agent.guardrails.token_budget import (
    REASON_CODE_CAP_TOKEN_BUDGET,
    budget,
)
from genial_agent.observability.stats import incr as stats_incr

# ``run_routed_turn`` est importé **lazy** dans ``run_guarded_turn``
# pour casser la boucle d'import : ``routing.py`` consomme
# ``guardrails.caps`` (via ``from genial_agent.guardrails.caps import
# …``, ce qui déclenche ``guardrails/__init__.py``), lequel ré-exporte
# ``run_guarded_turn`` pour le confort de S06. Sans import tardif, le
# cycle ``routing → caps → guardrails/__init__ → pipeline → routing``
# plante en ImportError au runtime.

logger = structlog.get_logger(__name__)

CRITIC_TIMEOUT_S = 10.0

# S09.7 Axe 3 C4 — Liste des reason_codes pour lesquels le pipeline
# émet un event ``cap_continuation_proposed`` après ``capped``. La UI
# Chainlit (S06) intercepte cet event pour afficher des actions
# « Continuer » / « Synthèse partielle » plutôt que de laisser
# l'utilisateur en dead-end conversationnel. Le contexte (vault inclus)
# est préservé sur le ``ConversationState`` session-scoped.
#
# Si un nouveau reason_code apparaît côté routing/token_budget, l'ajouter
# ici **et** dans le test paramétré
# ``test_continuation_event_supported_reason_codes``.
CONTINUATION_REASON_CODES = frozenset(
    {
        "cap_token_budget",
        "cap_tool_calls_per_turn",
        "cap_local_lookups_per_turn",
        "cap_wall_clock",
    }
)

# S09.7 amélioration 1 — auto-continuation backend sur les caps
# **compute pur** (zéro coût €). Quand un de ces reason_codes
# firefires sans que l'agent ait conclu, le pipeline relance
# automatiquement un nouveau ``run_routed_turn`` avec un message
# neutre, état préservé. Limité à 1 retry par turn user (évite la
# boucle infinie). Les caps "argent" (token_budget, tool_calls,
# wall_clock) restent dead-end côté backend — l'utilisateur reste
# souverain via le bouton UI ``cap_continuation_proposed``.
AUTO_CONTINUATION_REASON_CODES = frozenset({"cap_local_lookups_per_turn"})

_AUTO_CONTINUATION_PROMPT = "Continue depuis où tu t'es arrêté."


def _continuation_event(capped_event: dict[str, Any], session_id: str) -> dict[str, Any]:
    """Construit l'event ``cap_continuation_proposed`` à partir d'un
    ``capped`` reçu/émis. ``reason_code`` doit être dans
    ``CONTINUATION_REASON_CODES`` ; sinon l'event n'est PAS émis (la
    dispatch côté pipeline doit avoir filtré en amont)."""
    return {
        "type": "cap_continuation_proposed",
        "reason_code": capped_event.get("reason_code"),
        "reason": capped_event.get("reason", ""),
        "session_id": session_id,
    }


async def run_guarded_turn(
    state: ConversationState,
    user_message: str,
    session_id: str,
    system_prompt_override: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Pipeline complet avec les 6 couches garde-fous.

    Args:
        state: ``ConversationState`` par session Chainlit (S06 crée un
            state par ``cl.user_session``).
        user_message: input utilisateur brut (sera validé par l'input
            gate puis wrappé par S03 ``wrap_user_input``).
        session_id: identifiant de session (``cl.user_session.get("id")``
            côté S06) — clef du token budget.
        system_prompt_override: si fourni, remplace ``SYSTEM_PROMPT_AGENT``
            au niveau ``agent.run_turn`` (propagé via
            ``run_routed_turn``). Utilisé par S10 voice mode pour
            injecter le suffixe voice-friendly. **Non-breaking** :
            default ``None`` = pipeline d'origine inchangé.

    Yields:
        dict events. Superset du contrat S03/S04 + events garde-fous :

        - ``input_rejected`` : input refusé par C1.
        - ``capped`` (réémis par le pipeline si token budget dépassé).
        - ``hallucination_detected`` : orphan SIREN ou missing bilan date.
        - ``validator_degraded`` : texte dégradé par ``degrade``.
        - ``critic_pending`` : signal « attendez quelques ms ».
        - ``critic_result`` : résultat du critic (green/orange/red).
    """
    # Lazy pour casser le cycle d'import (cf. header du module).
    from genial_agent.routing import run_routed_turn

    # --- C1 Input gate ---
    gate = evaluate_input(user_message)
    if not gate.ok:
        logger.info(
            "pipeline_input_rejected",
            reason_code=gate.reason_code,
            input_length=len(user_message),
        )
        yield {
            "type": "input_rejected",
            "reason_code": gate.reason_code,
            "reason": gate.reason or "",
        }
        return

    # --- C4 Pre-turn token budget check ---
    if await budget.exhausted(session_id):
        logger.warning(
            "pipeline_token_budget_pre_exhausted",
            session_id=session_id,
        )
        capped_event = {
            "type": "capped",
            "reason_code": REASON_CODE_CAP_TOKEN_BUDGET,
            "reason": f"{budget.cap} tokens/session",
        }
        yield capped_event
        # S09.7 Axe 3 C4 : cap-as-UX-event. La UI propose Continuer /
        # Synthèse au lieu de dead-end. Le user devra reset le budget
        # côté UI (typiquement via "nouvelle conversation" ou un
        # bouton dédié) — le pipeline n'auto-reset pas (préserve
        # l'audit trail des budgets).
        yield _continuation_event(capped_event, session_id)
        return

    # --- Run routed turn(s) avec observation budget + collect sirens ---
    text_chunks: list[str] = []
    allowed_sirens: set[str] = set()
    budget_emitted = False

    # S09.7 amélioration 1 : auto-continuation backend sur cap_local_lookups.
    # Le pipeline traite une **queue** de messages utilisateur. La queue
    # part avec le message original ; si un cap zero-coût firefires sans
    # conclusion, on enqueue un message de continuation neutre.
    # ``auto_continued_once`` borne à 1 retry par turn user (évite boucle).
    turn_inputs: list[str] = [user_message]
    auto_continued_once = False

    # PEP 789 — un async generator imbriqué doit être explicitement
    # ``aclose()``-é dans un ``try/finally`` quand on l'itère depuis un
    # autre async generator. Sans ce filet, si le consumer (S06) ferme
    # ``run_guarded_turn`` (déconnexion utilisateur, exception en
    # amont), l'inner generator ``run_routed_turn`` se voit fermé par
    # GC plus tard, ce qui peut leaker ``state.lock`` (cf. invariant
    # I5 S03) ou des connexions Anthropic encore ouvertes. Cohérent
    # avec le pattern S04 ``run_routed_turn`` qui fait de même sur son
    # inner ``run_turn``.
    while turn_inputs:
        current_message = turn_inputs.pop(0)
        routed = run_routed_turn(
            state,
            current_message,
            system_prompt_override=system_prompt_override,
        )
        try:
            async for event in routed:
                # Forward tel quel (superset, pas de mutation).
                yield event

                etype = event.get("type")

                # S09.7 Axe 3 C4 — cap-as-UX-event. Pour CHAQUE event
                # ``capped`` émis (par run_routed_turn ou par nous), on
                # émet un ``cap_continuation_proposed`` pour que la UI
                # propose Continuer / Synthèse partielle.
                if etype == "capped":
                    rc = event.get("reason_code")
                    if rc in CONTINUATION_REASON_CODES:
                        yield _continuation_event(event, session_id)
                    # S09.7 amélioration 1 : auto-continuation
                    # backend sur cap_local_lookups (compute pur).
                    # 1 retry max par turn user. Le message neutre
                    # ``"Continue..."`` ne dicte aucune stratégie —
                    # l'agent décide ce qu'il fait avec son state +
                    # vault préservés.
                    if rc in AUTO_CONTINUATION_REASON_CODES and not auto_continued_once:
                        auto_continued_once = True
                        turn_inputs.append(_AUTO_CONTINUATION_PROMPT)
                        yield {
                            "type": "auto_continuation_started",
                            "reason_code": rc,
                            "session_id": session_id,
                        }
                        logger.info(
                            "pipeline_auto_continuation_started",
                            reason_code=rc,
                            session_id=session_id,
                        )

                if etype == "text":
                    text_chunks.append(event.get("content", ""))
                elif etype == "llm_meta":
                    in_tok = int(event.get("input_tokens") or 0)
                    out_tok = int(event.get("output_tokens") or 0)
                    await budget.add(session_id, in_tok, out_tok)
                    # S07 (B1 fix) — ``llm_meta`` est émis **par appel
                    # Claude** (chaque itération de ``agent.run_turn``, plus
                    # une 2ᵉ série en cas d'escalade Haiku→Sonnet). Le
                    # pipeline incrémente donc ``total_llm_calls`` (compteur
                    # bas niveau, utile au debug latence/coût) ; le compteur
                    # ``total_turns`` (1 par tour utilisateur) est porté par
                    # ``app.py:on_message`` qui voit lui le périmètre
                    # message-utilisateur. Bind contextvar du ``request_id``
                    # par-appel : chaque ``bind_contextvars`` écrase le
                    # précédent, c'est attendu (request_id par-appel).
                    #
                    # S09.7 — agrégation des compteurs prompt caching
                    # (cache_creation, cache_read). Cible mesurable :
                    # cache_read_tokens / input_tokens > 50 % à partir du
                    # 2ème round (cf. story §"Mesure prompt caching").
                    cache_creation = int(event.get("cache_creation_tokens") or 0)
                    cache_read = int(event.get("cache_read_tokens") or 0)
                    stats_incr(
                        total_llm_calls=1,
                        anthropic_input_tokens=in_tok,
                        anthropic_output_tokens=out_tok,
                        anthropic_cache_creation_tokens=cache_creation,
                        anthropic_cache_read_tokens=cache_read,
                    )
                    request_id = event.get("request_id")
                    if request_id:
                        structlog.contextvars.bind_contextvars(request_id=request_id)
                    if not budget_emitted and await budget.exhausted(session_id):
                        budget_emitted = True
                        capped_inflight = {
                            "type": "capped",
                            "reason_code": REASON_CODE_CAP_TOKEN_BUDGET,
                            "reason": f"{budget.cap} tokens/session",
                        }
                        yield capped_inflight
                        # S09.7 Axe 3 C4 — pendant qu'on est encore dans
                        # la boucle ``run_routed_turn``, on émet aussi le
                        # ``cap_continuation_proposed``. La boucle finit
                        # son itération en cours (S04 ne ``break`` pas sur
                        # ce capped) puis l'event ``end`` clôture proprement.
                        yield _continuation_event(capped_inflight, session_id)
                elif etype == "tool_result":
                    # Collecte des SIREN Luhn-valides dans les tool_results pour
                    # ``allowed_sirens`` du validator. ``content_preview`` est
                    # tronqué à 200 chars (contrat S03) — suffisant dans 99 %
                    # des cas car les SIREN Pappers sont en tête de payload
                    # (``{"siren": "...", ...}``). Cf. annexe B de la story.
                    preview = event.get("content_preview") or ""
                    allowed_sirens |= extract_sirens(preview, luhn_only=True)
                elif etype == "payload_offloaded":
                    # S09.5 — un payload MCP volumineux a été rangé dans le
                    # vault session. L'event est forwarded inchangé pour la
                    # UI (steps view S06) ; on incrémente le compteur S07.
                    stats_incr(payloads_offloaded_total=1)
                elif etype == "payload_inspected":
                    stats_incr(payload_inspects_total=1)
                elif etype == "payload_searched":
                    stats_incr(payload_searches_total=1)
        finally:
            await routed.aclose()

    # --- C5 Output validator ---
    final_text = "".join(text_chunks)
    result = validate_response(final_text, allowed_sirens)
    if result.issues:
        if result.orphan_sirens:
            yield {
                "type": "hallucination_detected",
                "reason_code": REASON_CODE_HALLUCINATION_ORPHAN_SIRENS,
                "orphan_sirens": list(result.orphan_sirens),
                "issues": list(result.issues),
            }
        if REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE in result.issues:
            yield {
                "type": "hallucination_detected",
                "reason_code": REASON_CODE_HALLUCINATION_MISSING_BILAN_DATE,
                "orphan_sirens": [],
                "issues": list(result.issues),
            }
        degraded_text, _needs_retry = degrade(result, final_text)
        yield {
            "type": "validator_degraded",
            "degraded_text": degraded_text,
            "issues": list(result.issues),
        }

    # --- C6 Haiku-critic async (non-bloquant, 10 s cap) ---
    yield {"type": "critic_pending"}
    critic_task = asyncio.create_task(critique_async(user_message, final_text))
    try:
        try:
            critic = await asyncio.wait_for(critic_task, timeout=CRITIC_TIMEOUT_S)
        except TimeoutError:
            logger.warning("pipeline_critic_timeout", session_id=session_id)
            critic = CriticResult(
                scope_ok=True,
                hallucination_risk="low",
                advisory_language=False,
                confidence=0.0,
                issues=["critic_timeout"],
            )
    finally:
        # Si le consumer ``aclose()`` le pipeline entre l'émission de
        # ``critic_pending`` et le yield de ``critic_result``, ou si
        # ``wait_for`` lève autre chose qu'un ``TimeoutError`` (annulation
        # parente), on s'assure que le ``critic_task`` ne fuite pas en
        # arrière-plan. ``cancel()`` est idempotent et un no-op si la
        # task est déjà terminée.
        if not critic_task.done():
            critic_task.cancel()
    yield {"type": "critic_result", **critic.to_event()}
