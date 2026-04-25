"""Tests des compteurs ``stats`` (S07 §"Stats : sync vs async")."""

from __future__ import annotations

import datetime as _dt
import time

import pytest

from genial_agent.observability import stats as s


def test_initial_state_is_zero() -> None:
    snap = s.snapshot()
    assert snap["total_turns"] == 0
    assert snap["total_llm_calls"] == 0
    assert snap["total_tool_calls"] == 0
    assert snap["pappers_calls_today"] == 0
    assert snap["anthropic_input_tokens"] == 0
    assert snap["anthropic_output_tokens"] == 0
    assert snap["errors"] == 0


def test_incr_and_snapshot_roundtrip() -> None:
    s.incr(total_turns=1, total_tool_calls=3, pappers_calls_today=3)
    snap = s.snapshot()
    assert snap["total_turns"] == 1
    assert snap["total_tool_calls"] == 3
    assert snap["pappers_calls_today"] == 3


def test_incr_accumulates() -> None:
    s.incr(total_turns=1)
    s.incr(total_turns=2)
    s.incr(total_turns=4)
    assert s.snapshot()["total_turns"] == 7


def test_incr_unknown_field_raises() -> None:
    with pytest.raises(AttributeError, match="unknown stat"):
        s.incr(total_nonsense=1)


def test_pappers_calls_today_accessor() -> None:
    s.incr(pappers_calls_today=42)
    assert s.pappers_calls_today() == 42


def test_pappers_today_rollover(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force un changement de jour : ``pappers_calls_today`` reset, les
    autres compteurs persistent."""
    s.incr(pappers_calls_today=5, total_tool_calls=10)
    assert s.pappers_calls_today() == 5

    # Avance la date d'un jour côté ``stats.datetime``.
    fake_today = _dt.datetime.now(_dt.UTC) + _dt.timedelta(days=1)

    class _FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001 — signature compat datetime
            return fake_today

    monkeypatch.setattr("genial_agent.observability.stats.datetime", _FakeDT)
    # Le 1er accès post-rollover doit reset pappers_calls_today, mais
    # PAS les autres compteurs (cumulatifs).
    assert s.pappers_calls_today() == 0
    assert s.snapshot()["total_tool_calls"] == 10


def test_uptime_grows() -> None:
    snap1 = s.snapshot()
    time.sleep(0.05)
    snap2 = s.snapshot()
    assert snap2["uptime_s"] >= snap1["uptime_s"]


def test_snapshot_keys_are_stable() -> None:
    """Régression : ``/stats`` est consommé par UptimeRobot et l'admin —
    le set de clés doit être stable. Une nouvelle clé ne casse rien,
    une suppression casserait l'admin → liste explicite."""
    snap = s.snapshot()
    expected_keys = {
        "started_at",
        "uptime_s",
        "total_turns",
        "total_llm_calls",
        "total_tool_calls",
        "pappers_calls_today",
        "pappers_calls_today_day",
        "anthropic_input_tokens",
        "anthropic_output_tokens",
        "errors",
    }
    assert expected_keys <= set(snap.keys())


def test_total_turns_and_total_llm_calls_are_independent() -> None:
    """Régression B1 : ``total_turns`` (par tour utilisateur) et
    ``total_llm_calls`` (par appel Claude) sont deux compteurs
    distincts. Le pipeline n'incrémente que ``total_llm_calls``, c'est
    ``app.py:on_message`` qui incrémente ``total_turns``.
    """
    s.incr(total_turns=2)  # 2 tours utilisateur servis
    s.incr(total_llm_calls=7)  # 7 appels Claude (chain de tool calls)
    snap = s.snapshot()
    assert snap["total_turns"] == 2
    assert snap["total_llm_calls"] == 7


def test_incr_typo_on_renamed_field_raises() -> None:
    """Régression B1 : un typo sur ``total_turns``/``total_llm_calls``
    lève. Garde-fou contre une régression silencieuse si on rebascule
    accidentellement le compteur dans le pipeline."""
    with pytest.raises(AttributeError, match="unknown stat"):
        s.incr(total_llmm_calls=1)  # typo intentionnel


def test_pipeline_does_not_increment_total_turns_static_check() -> None:
    """Régression B1 (static guard) : ``pipeline.py`` ne doit JAMAIS
    incrémenter ``total_turns``. Cette sémantique est portée
    exclusivement par ``app.py:on_message`` (1 incr par tour utilisateur).

    On ne peut pas se reposer sur les tests d'intégration end-to-end
    pour attraper une régression silencieuse (ex : un futur dev rebascule
    naïvement le ``stats_incr(total_turns=...)`` dans la branche
    ``llm_meta`` du pipeline) — ce test garde la frontière nette par
    inspection de la source.
    """
    import inspect
    import re

    from genial_agent.guardrails import pipeline as pipe_mod

    src = inspect.getsource(pipe_mod)
    # On cherche un appel effectif (kwarg) — pas un mot dans une
    # docstring ou un commentaire qui explique pourquoi le compteur
    # n'est PAS ici.
    assert not re.search(r"total_turns\s*=", src), (
        "pipeline.py ne doit pas appeler stats_incr(total_turns=...) "
        "(ce compteur est à la charge de app.py:on_message — cf. review B1)"
    )
    assert re.search(r"total_llm_calls\s*=", src), (
        "pipeline.py doit incrémenter total_llm_calls par llm_meta event"
    )


async def test_pipeline_increments_total_llm_calls_per_llm_meta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression B1 (dynamic) : un turn pipeline avec N appels Claude
    (chaîne de tool calls) → ``total_llm_calls += N`` ; ``total_turns``
    inchangé (porté par ``app.py``).
    """
    from genial_agent.agent import ConversationState
    from genial_agent.guardrails.critic import CriticResult
    from genial_agent.guardrails.pipeline import run_guarded_turn
    from tests.unit.test_S03_agent_loop import (
        _install_fake_anthropic,
        _install_fake_mcp,
        _message,
        _ScriptedTurn,
        _text,
    )

    # 1 turn utilisateur, 1 seul appel Claude (réponse texte directe).
    script = [
        _ScriptedTurn(
            text_chunks=["ok"],
            final=_message(stop_reason="end_turn", content=[_text("ok")]),
        )
    ]
    _install_fake_anthropic(monkeypatch, script)
    _install_fake_mcp(monkeypatch)

    async def _green_critic(q: str, r: str) -> CriticResult:
        return CriticResult(True, "low", False, 0.9, [])

    monkeypatch.setattr("genial_agent.guardrails.pipeline.critique_async", _green_critic)

    state = ConversationState()
    events = [ev async for ev in run_guarded_turn(state, "Fiche LVMH", "s_b1")]
    llm_meta_count = sum(1 for e in events if e.get("type") == "llm_meta")
    assert llm_meta_count >= 1

    snap = s.snapshot()
    assert snap["total_llm_calls"] == llm_meta_count, (
        "total_llm_calls doit suivre 1:1 le nombre d'events llm_meta"
    )
    assert snap["total_turns"] == 0, (
        "Le pipeline ne doit jamais incrémenter total_turns "
        "(régression B1 — porté par app.py:on_message)"
    )


def test_reset_for_tests_reinit_counters() -> None:
    s.incr(total_turns=99, errors=42)
    s.reset_for_tests()
    snap = s.snapshot()
    assert snap["total_turns"] == 0
    assert snap["errors"] == 0
