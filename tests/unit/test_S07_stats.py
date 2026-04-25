"""Tests des compteurs ``stats`` (S07 §"Stats : sync vs async")."""

from __future__ import annotations

import datetime as _dt
import time

import pytest

from genial_agent.observability import stats as s


def test_initial_state_is_zero() -> None:
    snap = s.snapshot()
    assert snap["total_turns"] == 0
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
        "total_tool_calls",
        "pappers_calls_today",
        "pappers_calls_today_day",
        "anthropic_input_tokens",
        "anthropic_output_tokens",
        "errors",
    }
    assert expected_keys <= set(snap.keys())


def test_reset_for_tests_reinit_counters() -> None:
    s.incr(total_turns=99, errors=42)
    s.reset_for_tests()
    snap = s.snapshot()
    assert snap["total_turns"] == 0
    assert snap["errors"] == 0
