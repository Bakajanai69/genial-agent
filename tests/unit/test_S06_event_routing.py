"""Tests unitaires du dispatcher S06 (contrat events ↔ rendu UI).

On stubbe ``cl.Message`` et ``cl.Step`` — l'objectif est de prouver que
le dispatcher fait les bonnes opérations de manipulation d'état, pas de
tester Chainlit lui-même (out-of-scope MVP : pas de pytest-chainlit en
2026 stable).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from genial_agent.ui.events import TurnState, dispatch_event


def _stub_msg() -> MagicMock:
    """``cl.Message`` mocké : send / stream_token / update / remove
    sont des AsyncMock, ``content`` est mutable string."""
    msg = MagicMock(name="cl.Message")
    msg.content = ""
    msg.send = AsyncMock()
    msg.stream_token = AsyncMock()
    msg.update = AsyncMock()
    msg.remove = AsyncMock()
    return msg


@pytest.fixture
def fake_msg() -> MagicMock:
    return _stub_msg()


@pytest.fixture
def state(fake_msg: MagicMock) -> TurnState:
    return TurnState(msg=fake_msg)


@pytest.fixture
def patched_message_class(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Patch ``cl.Message`` pour capturer les bulles système créées par
    le dispatcher (input_rejected, escalation, capped, end api_error).

    Renvoie une liste qui se remplit avec les ``kwargs`` (``content``,
    ``author``, ``type``) à chaque instanciation."""
    sent_kwargs: list[dict[str, Any]] = []

    def _factory(*args: Any, **kwargs: Any) -> MagicMock:
        # On capture aussi les positional args si jamais le code en
        # passe par la suite (à date, S06 ne passe que des kwargs).
        if args:
            kwargs.setdefault("content", args[0])
        sent_kwargs.append(kwargs)
        instance = _stub_msg()
        instance.content = kwargs.get("content", "")
        return instance

    import genial_agent.ui.events as events_mod

    monkeypatch.setattr(events_mod.cl, "Message", MagicMock(side_effect=_factory))
    return sent_kwargs


# ---------- Streaming + routing initial ----------


async def test_text_event_streams_to_msg(state: TurnState) -> None:
    await dispatch_event({"type": "text", "content": "Hello "}, state)
    state.msg.stream_token.assert_awaited_once_with("Hello ")


async def test_text_event_empty_content_does_not_crash(state: TurnState) -> None:
    await dispatch_event({"type": "text"}, state)
    state.msg.stream_token.assert_awaited_once_with("")


async def test_routing_initial_records_tier(state: TurnState) -> None:
    await dispatch_event(
        {"type": "routing_initial", "tier": "sonnet", "reason": "keyword"},
        state,
    )
    assert state.initial_tier == "sonnet"


async def test_routing_done_overrides_state(state: TurnState) -> None:
    await dispatch_event(
        {
            "type": "routing_done",
            "model_used": "sonnet",
            "escalated": True,
            "escalation_mode": "self",
            "escalation_reason_code": "self",
            "escalation_reason": "needed deeper analysis",
            "capped": False,
            "tool_calls_count": 3,
        },
        state,
    )
    assert state.model_used == "sonnet"
    assert state.escalated is True
    assert state.escalation_mode == "self"


# ---------- Validator + critic ----------


async def test_validator_degraded_overrides_msg_content_with_linkified(
    state: TurnState,
) -> None:
    await dispatch_event(
        {
            "type": "validator_degraded",
            "degraded_text": "Réponse + disclaimer SIREN 775670417",
            "issues": ["hallucination_orphan_sirens"],
        },
        state,
    )
    assert "pappers.fr/entreprise/775670417" in state.final_text
    assert state.msg.content == state.final_text
    # ``linkify_applied`` doit être positionné — sinon le post-loop
    # ``app.on_message`` re-linkifierait et provoquerait un double-encodage
    # Markdown sur les SIREN déjà entourés de ``[...](...)`` (bug B1
    # de la review S06).
    assert state.linkify_applied is True
    state.msg.update.assert_awaited()


async def test_critic_result_does_not_set_linkify_applied(state: TurnState) -> None:
    """Régression bug B1 : ``critic_result`` ne doit PAS positionner
    ``linkify_applied``. Sinon le post-loop ``app.on_message`` skipperait
    le linkify final et les SIREN du chemin nominal (sans
    ``validator_degraded``) resteraient en texte brut."""
    state.msg.content = "Texte streamé avec SIREN 775670417"
    await dispatch_event(
        {
            "type": "critic_result",
            "color": "green",
            "confidence": 0.92,
            "scope_ok": True,
            "hallucination_risk": "low",
            "advisory_language": False,
            "issues": [],
        },
        state,
    )
    # Le critic ne touche pas au flag — l'app.py post-loop devra
    # linkifier lui-même.
    assert state.linkify_applied is False
    # Le SIREN n'est PAS encore linkifié (c'est le job du post-loop).
    assert "pappers.fr" not in state.msg.content


async def test_default_state_linkify_not_applied(state: TurnState) -> None:
    """Sanity : un ``TurnState`` neuf a ``linkify_applied=False``."""
    assert state.linkify_applied is False


async def test_critic_result_appends_badge_green(state: TurnState) -> None:
    state.final_text = "Texte final"
    state.msg.content = "Texte final"
    await dispatch_event(
        {
            "type": "critic_result",
            "color": "green",
            "confidence": 0.92,
            "scope_ok": True,
            "hallucination_risk": "low",
            "advisory_language": False,
            "issues": [],
        },
        state,
    )
    assert "✓" in state.msg.content
    assert "92%" in state.msg.content
    state.msg.update.assert_awaited()


async def test_critic_result_appends_issues_when_present(state: TurnState) -> None:
    state.final_text = "Texte final"
    state.msg.content = "Texte final"
    await dispatch_event(
        {
            "type": "critic_result",
            "color": "orange",
            "confidence": 0.65,
            "scope_ok": True,
            "hallucination_risk": "low",
            "advisory_language": False,
            "issues": ["scope_drift", "tone_advisory", "missing_date", "extra_issue_4"],
        },
        state,
    )
    assert "⚠" in state.msg.content
    assert "65%" in state.msg.content
    # Les 3 premières issues seulement (pas la 4ème).
    assert "scope_drift" in state.msg.content
    assert "missing_date" in state.msg.content
    assert "extra_issue_4" not in state.msg.content


async def test_critic_result_red(state: TurnState) -> None:
    state.final_text = "Texte"
    state.msg.content = "Texte"
    await dispatch_event(
        {
            "type": "critic_result",
            "color": "red",
            "confidence": 0.30,
            "scope_ok": False,
            "hallucination_risk": "high",
            "advisory_language": False,
            "issues": [],
        },
        state,
    )
    assert "✗" in state.msg.content
    assert "30%" in state.msg.content


async def test_critic_result_initializes_final_text_from_msg_content(
    state: TurnState,
) -> None:
    """Si ``validator_degraded`` n'a pas tourné, ``final_text`` est vide.
    Le critic doit alors lire ``msg.content`` pour ne pas effacer le
    streaming text déjà rendu."""
    state.msg.content = "Réponse streamée brute"
    assert state.final_text == ""
    await dispatch_event(
        {
            "type": "critic_result",
            "color": "green",
            "confidence": 0.90,
            "scope_ok": True,
            "hallucination_risk": "low",
            "advisory_language": False,
            "issues": [],
        },
        state,
    )
    assert "Réponse streamée brute" in state.msg.content
    assert "90%" in state.msg.content


# ---------- Input gate + cap ----------


async def test_input_rejected_removes_msg_and_marks_state(
    state: TurnState, patched_message_class: list[Any]
) -> None:
    await dispatch_event(
        {
            "type": "input_rejected",
            "reason_code": "input_injection",
            "reason": "regex match on jailbreak token",
        },
        state,
    )
    state.msg.remove.assert_awaited()
    assert state.input_rejected is True
    assert state.end_reason == "input_rejected"
    # Une bulle système a été créée pour informer l'utilisateur.
    assert len(patched_message_class) == 1
    assert "input_injection" in patched_message_class[0]["content"]
    assert patched_message_class[0]["author"] == "Garde-fou"


async def test_capped_emits_system_message(
    state: TurnState, patched_message_class: list[Any]
) -> None:
    await dispatch_event(
        {
            "type": "capped",
            "reason_code": "cap_token_budget",
            "reason": "100000 tokens/session",
        },
        state,
    )
    assert len(patched_message_class) == 1
    assert "Cap atteint" in patched_message_class[0]["content"]
    assert "cap_token_budget" in patched_message_class[0]["content"]


# ---------- Tool steps ----------


async def test_tool_use_then_tool_result_pairs_step(
    state: TurnState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``tool_use`` ouvre une step (input set), ``tool_result`` la ferme
    (output set) — la step n'est plus dans ``step_by_id`` après."""
    fake_step_instance = MagicMock(name="cl.Step")
    fake_step_instance.input = None
    fake_step_instance.output = None
    fake_step_instance.is_error = False
    fake_step_instance.__aenter__ = AsyncMock(return_value=fake_step_instance)
    fake_step_instance.__aexit__ = AsyncMock(return_value=None)

    import genial_agent.ui.events as events_mod

    monkeypatch.setattr(
        events_mod.cl,
        "Step",
        MagicMock(return_value=fake_step_instance),
    )

    await dispatch_event(
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "sirenisateur",
            "input": {"company_name": "LVMH", "country_code": "FR"},
        },
        state,
    )
    fake_step_instance.__aenter__.assert_awaited_once()
    assert fake_step_instance.input == {"company_name": "LVMH", "country_code": "FR"}
    assert "tu_1" in state.step_by_id

    await dispatch_event(
        {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "is_error": False,
            "content_preview": '{"siren": "775670417"}',
        },
        state,
    )
    fake_step_instance.__aexit__.assert_awaited_once()
    assert "tu_1" not in state.step_by_id
    assert fake_step_instance.output == '{"siren": "775670417"}'
    # Tracker a enregistré l'input et le preview.
    assert state.tracker.tool_use_inputs == [{"company_name": "LVMH", "country_code": "FR"}]
    assert state.tracker.tool_result_previews == ['{"siren": "775670417"}']


async def test_tool_result_marks_error_on_step(
    state: TurnState, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_step = MagicMock(name="cl.Step")
    fake_step.input = None
    fake_step.output = None
    fake_step.is_error = False
    fake_step.__aenter__ = AsyncMock(return_value=fake_step)
    fake_step.__aexit__ = AsyncMock(return_value=None)

    import genial_agent.ui.events as events_mod

    monkeypatch.setattr(events_mod.cl, "Step", MagicMock(return_value=fake_step))

    await dispatch_event(
        {"type": "tool_use", "id": "tu_err", "name": "comptes-entreprise", "input": {}},
        state,
    )
    await dispatch_event(
        {
            "type": "tool_result",
            "tool_use_id": "tu_err",
            "is_error": True,
            "content_preview": '{"error": "credits_exhausted"}',
        },
        state,
    )
    assert fake_step.is_error is True
    assert "tu_err" not in state.step_by_id


async def test_tool_result_for_unknown_id_does_not_crash(state: TurnState) -> None:
    """Si pour une raison ou une autre on reçoit un ``tool_result`` sans
    ``tool_use`` préalable (pipeline mal raccordé en futur), on log et
    on continue — le tracker garde quand même le preview."""
    await dispatch_event(
        {
            "type": "tool_result",
            "tool_use_id": "tu_unknown",
            "is_error": False,
            "content_preview": '{"siren": "775670417"}',
        },
        state,
    )
    assert state.tracker.tool_result_previews == ['{"siren": "775670417"}']


# ---------- Escalation + end ----------


async def test_escalation_self_emits_routing_message_and_flips_state(
    state: TurnState, patched_message_class: list[Any]
) -> None:
    await dispatch_event(
        {
            "type": "escalation",
            "reason_code": "self",
            "reason": "needed deeper analysis",
            "mode": "self",
        },
        state,
    )
    assert state.escalated is True
    assert state.escalation_mode == "self"
    assert len(patched_message_class) == 1
    msg_content = patched_message_class[0]["content"]
    assert "Haiku a demandé Sonnet" in msg_content


async def test_escalation_forced_emits_routing_message(
    state: TurnState, patched_message_class: list[Any]
) -> None:
    await dispatch_event(
        {
            "type": "escalation",
            "reason_code": "cap_tool_calls_per_turn",
            "reason": "5 tool calls",
            "mode": "forced",
        },
        state,
    )
    assert state.escalation_mode == "forced"
    msg_content = patched_message_class[0]["content"]
    assert "Cap déclenché" in msg_content


async def test_end_event_api_error_emits_banner(
    state: TurnState, patched_message_class: list[Any]
) -> None:
    await dispatch_event(
        {"type": "end", "tool_calls_count": 0, "reason": "api_error"},
        state,
    )
    assert state.end_reason == "api_error"
    assert len(patched_message_class) == 1
    assert "Erreur API Claude" in patched_message_class[0]["content"]


async def test_end_event_normal_silent(state: TurnState, patched_message_class: list[Any]) -> None:
    """Sur ``end(reason='end_turn')``, pas d'UI dédiée — le badge final
    + le contenu du msg suffisent."""
    await dispatch_event(
        {"type": "end", "tool_calls_count": 2, "reason": "end_turn"},
        state,
    )
    assert state.end_reason == "end_turn"
    assert len(patched_message_class) == 0


async def test_end_event_rate_limited(state: TurnState, patched_message_class: list[Any]) -> None:
    await dispatch_event(
        {"type": "end", "tool_calls_count": 0, "reason": "rate_limited"},
        state,
    )
    assert "Limite de débit" in patched_message_class[0]["content"]


# ---------- Forward-compat ----------


async def test_unknown_event_silently_ignored(state: TurnState) -> None:
    """Forward-compat S07/S10 : un event inconnu ne crashe pas la UI."""
    await dispatch_event({"type": "future_event_from_S07", "foo": "bar"}, state)
    state.msg.stream_token.assert_not_called()
    state.msg.update.assert_not_called()


async def test_llm_meta_silent(state: TurnState) -> None:
    """``llm_meta`` est consommé par le pipeline (token budget) et S07
    pour les stats — la UI ne doit rien afficher."""
    await dispatch_event(
        {
            "type": "llm_meta",
            "model": "claude-haiku-4-5",
            "input_tokens": 500,
            "output_tokens": 200,
            "request_id": "req_abc",
            "latency_ms": 800,
            "stop_reason": "end_turn",
        },
        state,
    )
    state.msg.stream_token.assert_not_called()
    state.msg.update.assert_not_called()


async def test_critic_pending_silent(state: TurnState) -> None:
    """MVP : ``critic_pending`` est silencieux."""
    state.msg.content = "before"
    await dispatch_event({"type": "critic_pending"}, state)
    assert state.msg.content == "before"
    state.msg.update.assert_not_called()


async def test_hallucination_detected_silent(state: TurnState) -> None:
    """``hallucination_detected`` est informatif — c'est
    ``validator_degraded`` qui pose le disclaimer visible."""
    await dispatch_event(
        {
            "type": "hallucination_detected",
            "reason_code": "hallucination_orphan_sirens",
            "orphan_sirens": ["123456789"],
            "issues": ["hallucination_orphan_sirens"],
        },
        state,
    )
    state.msg.update.assert_not_called()


# ── S09.7 UI : distinction réflexion vs réponse finale ───────────────


async def test_text_buffer_accumulates_per_section(state: TurnState) -> None:
    """Les chunks text sont accumulés dans ``current_text_buffer`` en
    plus du streaming UI live."""
    await dispatch_event({"type": "text", "content": "Je vais "}, state)
    await dispatch_event({"type": "text", "content": "rechercher..."}, state)
    assert state.current_text_buffer == "Je vais rechercher..."
    # Le streaming live a bien eu lieu aussi.
    assert state.msg.stream_token.call_count == 2


async def test_tool_use_flushes_buffer_to_text_sections(
    state: TurnState, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un ``tool_use`` event flush le buffer courant dans
    ``text_sections`` (= raisonnement intermédiaire)."""
    fake_step_instance = MagicMock(name="cl.Step")
    fake_step_instance.__aenter__ = AsyncMock(return_value=fake_step_instance)
    fake_step_instance.__aexit__ = AsyncMock(return_value=None)
    import genial_agent.ui.events as events_mod

    monkeypatch.setattr(
        events_mod.cl,
        "Step",
        MagicMock(return_value=fake_step_instance),
    )

    await dispatch_event({"type": "text", "content": "Je vais rechercher LVMH"}, state)
    await dispatch_event(
        {"type": "tool_use", "id": "tu1", "name": "sirenisateur", "input": {}},
        state,
    )
    assert state.text_sections == ["Je vais rechercher LVMH"]
    assert state.current_text_buffer == ""


async def test_format_msg_with_reasoning_sections() -> None:
    """La fonction post-process wrappe les sections de raisonnement en
    blockquote *italique* et garde la réponse finale en clair."""
    from genial_agent.ui.events import format_msg_with_reasoning_sections

    state = TurnState(msg=_stub_msg())
    state.text_sections = [
        "Je vais rechercher le SIREN de LVMH",
        "Maintenant je vais récupérer les détails complets",
    ]
    state.current_text_buffer = "## Fiche LVMH\n- SIREN 775670417"

    result = format_msg_with_reasoning_sections(state)
    assert result is not None
    assert "> 💭 *Je vais rechercher le SIREN de LVMH*" in result
    assert "> 💭 *Maintenant je vais récupérer les détails complets*" in result
    # La réponse finale reste en clair (pas wrappée)
    assert "## Fiche LVMH\n- SIREN 775670417" in result
    # La réponse finale arrive APRÈS les sections de raisonnement
    assert result.index("Fiche LVMH") > result.index("Je vais rechercher")


async def test_format_msg_returns_none_if_no_chaining() -> None:
    """Si l'agent répond direct sans tool_use, ``text_sections`` est
    vide → pas de reformatage."""
    from genial_agent.ui.events import format_msg_with_reasoning_sections

    state = TurnState(msg=_stub_msg())
    state.current_text_buffer = "Réponse simple sans tool"
    assert format_msg_with_reasoning_sections(state) is None


async def test_format_msg_applied_on_routing_done_before_validator() -> None:
    """S09.7 hotfix : le rewrappage est désormais appliqué dans
    ``dispatch_event(routing_done)`` AVANT que validator/linkify
    ne touchent au msg. Ne dépend plus de ``linkify_applied`` (le
    précédent retour None sur cette condition manquait son objectif :
    le rewrappage devait s'appliquer dans tous les cas où il y a eu
    chaînage de tools)."""
    from genial_agent.ui.events import format_msg_with_reasoning_sections

    state = TurnState(msg=_stub_msg())
    state.text_sections = ["raisonnement"]
    state.current_text_buffer = "réponse"
    state.linkify_applied = True  # ne doit plus empêcher le rewrappage
    result = format_msg_with_reasoning_sections(state)
    assert result is not None
    assert "> 💭 *raisonnement*" in result
    assert "réponse" in result
