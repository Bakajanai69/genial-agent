"""S09.7 Axe 3 C4 — Cap-as-UX-event.

Quand un cap (token_budget, tool_calls, local_lookups, wall_clock)
firefires et émet un event ``capped``, le pipeline doit IMMÉDIATEMENT
émettre un event ``cap_continuation_proposed`` non-bloquant pour que
la UI Chainlit puisse exposer un bouton « Continuer » / « Synthèse
partielle » plutôt que de laisser l'utilisateur en dead-end.

Le contexte (vault inclus) est préservé sur le ``ConversationState``
session-scoped → le clic « Continuer » relance ``run_guarded_turn``
sur le même state sans repartir de zéro.
"""

from __future__ import annotations

from typing import Any

import pytest

from genial_agent.agent import ConversationState
from genial_agent.guardrails.pipeline import run_guarded_turn
from genial_agent.guardrails.token_budget import REASON_CODE_CAP_TOKEN_BUDGET


def _get_budget() -> Any:
    """Fetch dynamique : la fixture autouse ``_fresh_budget`` swap le
    singleton APRÈS l'import top-level du test, on doit aller le
    chercher à chaque appel."""
    from genial_agent.guardrails import pipeline as pipe_mod

    return pipe_mod.budget


async def _drain(state: ConversationState, prompt: str, sid: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    gen = run_guarded_turn(state, prompt, sid)
    try:
        async for ev in gen:
            events.append(ev)
    finally:
        await gen.aclose()
    return events


async def test_cap_token_budget_emits_continuation_event() -> None:
    """Un ``capped(token_budget)`` pre-turn génère immédiatement un
    ``cap_continuation_proposed`` avec le bon ``reason_code``."""
    sid = "test_continuation_token"
    bud = _get_budget()
    # Saturer le budget AVANT le call : le pre-turn check fire le cap
    # tout de suite, on évite d'engager du réseau.
    await bud.add(sid, bud.cap, 0)

    events = await _drain(ConversationState(), "Quelle est la fiche de LVMH ?", sid)
    types = [e.get("type") for e in events]

    assert "capped" in types
    assert "cap_continuation_proposed" in types
    # L'event continuation doit suivre immédiatement le capped (même
    # ``reason_code``).
    capped_idx = types.index("capped")
    cont_idx = types.index("cap_continuation_proposed")
    assert cont_idx == capped_idx + 1
    assert events[cont_idx].get("reason_code") == REASON_CODE_CAP_TOKEN_BUDGET


async def test_cap_continuation_carries_session_id() -> None:
    """L'event ``cap_continuation_proposed`` expose ``session_id`` pour
    que le handler Chainlit puisse retrouver le bon state."""
    sid = "test_continuation_session_id"
    bud = _get_budget()
    await bud.add(sid, bud.cap, 0)

    events = await _drain(ConversationState(), "Quelle est la fiche de LVMH ?", sid)
    cont = next(e for e in events if e.get("type") == "cap_continuation_proposed")
    assert cont.get("session_id") == sid


async def test_no_continuation_event_when_input_rejected() -> None:
    """Quand l'input gate rejette en amont, aucun continuation event
    n'est émis (rien à continuer)."""
    sid = "test_no_continuation_when_rejected"
    # Input vide → input gate rejette, return immédiat sans capped.
    events = await _drain(ConversationState(), "", sid)
    types = [e.get("type") for e in events]
    assert "cap_continuation_proposed" not in types


@pytest.mark.parametrize(
    "reason_code",
    [
        "cap_token_budget",
        "cap_tool_calls_per_turn",
        "cap_local_lookups_per_turn",
        "cap_wall_clock",
    ],
)
def test_continuation_event_supported_reason_codes(reason_code: str) -> None:
    """Sanity : la liste des reason_codes que le pipeline ré-émettra
    est documentée. Si on en ajoute un nouveau côté routing, ce test
    doit être étendu pour qu'on n'oublie pas le ``cap_continuation_proposed``
    associé."""
    from genial_agent.guardrails.pipeline import CONTINUATION_REASON_CODES

    assert reason_code in CONTINUATION_REASON_CODES


# ---- S09.7 amélioration 1 : auto-continuation backend ----


def test_auto_continuation_only_for_local_lookups() -> None:
    """Sanity : seul ``cap_local_lookups_per_turn`` (compute pur, zéro
    coût €) déclenche l'auto-continuation backend. Les caps "argent"
    (token_budget, tool_calls, wall_clock) restent dead-end côté
    backend — l'utilisateur reste souverain via le bouton UI."""
    from genial_agent.guardrails.pipeline import AUTO_CONTINUATION_REASON_CODES

    assert frozenset({"cap_local_lookups_per_turn"}) == AUTO_CONTINUATION_REASON_CODES
    # Caps monétaires NON auto-continués
    assert "cap_token_budget" not in AUTO_CONTINUATION_REASON_CODES
    assert "cap_tool_calls_per_turn" not in AUTO_CONTINUATION_REASON_CODES
    assert "cap_wall_clock" not in AUTO_CONTINUATION_REASON_CODES
