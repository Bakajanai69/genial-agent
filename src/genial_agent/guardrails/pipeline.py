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


async def run_guarded_turn(
    state: ConversationState,
    user_message: str,
    session_id: str,
) -> AsyncIterator[dict[str, Any]]:
    """Pipeline complet avec les 6 couches garde-fous.

    Args:
        state: ``ConversationState`` par session Chainlit (S06 crée un
            state par ``cl.user_session``).
        user_message: input utilisateur brut (sera validé par l'input
            gate puis wrappé par S03 ``wrap_user_input``).
        session_id: identifiant de session (``cl.user_session.get("id")``
            côté S06) — clef du token budget.

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
        yield {
            "type": "capped",
            "reason_code": REASON_CODE_CAP_TOKEN_BUDGET,
            "reason": f"{budget.cap} tokens/session",
        }
        return

    # --- Run routed turn avec observation budget + collect sirens ---
    text_chunks: list[str] = []
    allowed_sirens: set[str] = set()
    budget_emitted = False

    # PEP 789 — un async generator imbriqué doit être explicitement
    # ``aclose()``-é dans un ``try/finally`` quand on l'itère depuis un
    # autre async generator. Sans ce filet, si le consumer (S06) ferme
    # ``run_guarded_turn`` (déconnexion utilisateur, exception en
    # amont), l'inner generator ``run_routed_turn`` se voit fermé par
    # GC plus tard, ce qui peut leaker ``state.lock`` (cf. invariant
    # I5 S03) ou des connexions Anthropic encore ouvertes. Cohérent
    # avec le pattern S04 ``run_routed_turn`` qui fait de même sur son
    # inner ``run_turn``.
    routed = run_routed_turn(state, user_message)
    try:
        async for event in routed:
            # Forward tel quel (superset, pas de mutation).
            yield event

            etype = event.get("type")
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
                stats_incr(
                    total_llm_calls=1,
                    anthropic_input_tokens=in_tok,
                    anthropic_output_tokens=out_tok,
                )
                request_id = event.get("request_id")
                if request_id:
                    structlog.contextvars.bind_contextvars(request_id=request_id)
                if not budget_emitted and await budget.exhausted(session_id):
                    budget_emitted = True
                    yield {
                        "type": "capped",
                        "reason_code": REASON_CODE_CAP_TOKEN_BUDGET,
                        "reason": f"{budget.cap} tokens/session",
                    }
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
