"""Tests du mode dégradé crédits Pappers (S07 + cahier §17.2)."""

from __future__ import annotations

import pytest

from genial_agent import mcp_pappers
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
from genial_agent.observability import credit_guard
from genial_agent.observability import stats as s


def test_not_degraded_initially() -> None:
    assert credit_guard.degraded() is False
    assert credit_guard.remaining() == DAILY_PAPPERS_CREDITS_CAP


def test_degraded_after_cap() -> None:
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP)
    assert credit_guard.degraded() is True
    assert credit_guard.remaining() == 0


def test_degraded_when_above_cap() -> None:
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP + 50)
    assert credit_guard.degraded() is True
    assert credit_guard.remaining() == 0


def test_remaining_below_cap() -> None:
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP - 7)
    assert credit_guard.remaining() == 7
    assert credit_guard.degraded() is False


def test_mcp_pappers_resolves_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vérifie que la résolution paresseuse de S02 (review C4) branche
    bien notre fonction maintenant qu'``observability`` existe.

    Le contrat S02 : ``_is_degraded()`` tente l'import de
    ``observability.credit_guard.degraded`` une seule fois et le
    mémoïse. Il faut vider le cache de mémoïsation avant chaque test
    pour exercer le path d'import.
    """
    mcp_pappers._reset_degraded_cache()
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP)
    assert mcp_pappers._is_degraded() is True


def test_mcp_pappers_not_degraded_below_cap() -> None:
    mcp_pappers._reset_degraded_cache()
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP - 1)
    assert mcp_pappers._is_degraded() is False
