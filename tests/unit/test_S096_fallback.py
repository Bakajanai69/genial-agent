"""Tests S09.6 — Fallback B3 (WORKAROUND_HINTS).

Quand un tool listé dans ``WORKAROUND_HINTS`` lève ``CreditsExhausted``,
``call_tool`` ne propage pas l'exception : il retourne un ``tool_result``
synthétique avec ``isError=True`` + hint que l'agent peut interpréter
pour rebondir vers un tool alternatif.

Couvre les deux chemins de ``CreditsExhausted`` :

1. Mode dégradé (cap journalier crédits Pappers atteint).
2. Erreur métier Pappers ("crédits insuffisants" dans le payload).
"""

from __future__ import annotations

import json

import pytest

import genial_agent.mcp_pappers as mcp_pappers
from genial_agent.mcp_cache import cache
from genial_agent.mcp_pappers import (
    WORKAROUND_HINTS,
    CreditsExhausted,
    PappersToolError,
    call_tool,
)


@pytest.fixture(autouse=True)
def _reset_cache_and_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache propre + mode dégradé désactivé par défaut. Réinitialisé
    après chaque test pour ne pas leaker d'état entre tests."""
    cache.clear()
    mcp_pappers._reset_degraded_cache()
    # Empêche l'incrémentation des stats pour ne pas polluer un autre test.
    monkeypatch.setattr(
        "genial_agent.observability.stats.incr",
        lambda **kwargs: None,
    )


def test_workaround_hints_only_targets_comptes_entreprise() -> None:
    """Scope minimal du workaround (justification : retrait facile quand
    Pappers fixe le bug). Si on ajoute un tool, le test casse → forcer
    l'auteur à se demander si c'est intentionnel."""
    assert set(WORKAROUND_HINTS.keys()) == {"comptes-entreprise"}


def test_workaround_hint_mentions_recherche_entreprises() -> None:
    """Le hint doit guider l'agent vers ``recherche-entreprises`` avec
    les ``return_fields`` minimaux pour récupérer le CA headline."""
    hint = WORKAROUND_HINTS["comptes-entreprise"]
    assert "recherche-entreprises" in hint
    assert "return_fields" in hint
    assert "chiffre_affaires" in hint
    assert "resultat" in hint


async def test_credits_exhausted_on_listed_tool_returns_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path 1 : ``_invoke_tool_live`` retourne un payload Pappers qui
    contient ``{"error": "crédits insuffisants"}`` → ``CreditsExhausted``
    levé après ``_extract_business_error`` → intercepté par B3 →
    ``call_tool`` retourne un ``tool_result`` avec ``isError=True``."""

    async def _fake_invoke(name: str, args: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "crédits insuffisants"})}],
            "isError": False,
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _fake_invoke)

    result = await call_tool("comptes-entreprise", {"siren": "775670417", "annee": "2023"})

    assert result["isError"] is True
    assert isinstance(result["content"], list)
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert "error" in parsed
    assert "workaround_hint" in parsed
    assert "recherche-entreprises" in parsed["workaround_hint"]


async def test_credits_exhausted_on_unlisted_tool_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path 1 sur un tool sans workaround (e.g. cartographie-entreprise)
    → l'exception ``CreditsExhausted`` est propagée telle quelle, l'agent
    s'en sort via la règle "refus poli" du system prompt §8."""

    async def _fake_invoke(name: str, args: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "crédits insuffisants"})}],
            "isError": False,
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _fake_invoke)

    with pytest.raises(CreditsExhausted):
        await call_tool("cartographie-entreprise", {"siren": "775670417"})


async def test_degraded_mode_returns_tool_result_for_listed_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Path 2 : mode dégradé activé (cap journalier atteint) sur un tool
    listé → CreditsExhausted intercepté → tool_result synthétique."""

    monkeypatch.setattr(mcp_pappers, "_is_degraded", lambda: True)

    result = await call_tool("comptes-entreprise", {"siren": "775670417", "annee": "2024"})

    assert result["isError"] is True
    parsed = json.loads(result["content"][0]["text"])
    assert "Cap crédits Pappers atteint" in parsed["error"]
    assert parsed["workaround_hint"] == WORKAROUND_HINTS["comptes-entreprise"]


async def test_degraded_mode_propagates_for_unlisted_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mode dégradé sur un tool sans workaround → exception remontée."""

    monkeypatch.setattr(mcp_pappers, "_is_degraded", lambda: True)

    with pytest.raises(CreditsExhausted):
        await call_tool("sirenisateur", {"company_name": "LVMH", "country_code": "FR"})


async def test_non_credits_business_error_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Une erreur métier non-crédits (e.g. SIREN invalide) ne doit PAS
    être interceptée par B3 — elle doit lever ``PappersToolError`` qui
    remonte à l'agent comme une vraie erreur."""

    async def _fake_invoke(name: str, args: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "SIREN invalide"})}],
            "isError": False,
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _fake_invoke)

    with pytest.raises(PappersToolError):
        await call_tool("comptes-entreprise", {"siren": "000000000", "annee": "2024"})


async def test_successful_call_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-régression : un appel qui réussit normalement passe par le
    chemin standard, pas par le workaround."""

    async def _fake_invoke(name: str, args: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps({"siren": "775670417"})}],
            "isError": False,
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _fake_invoke)

    result = await call_tool("comptes-entreprise", {"siren": "775670417", "annee": "2024"})
    # Pas d'isError, payload tel quel.
    assert result.get("isError") is False
    assert "workaround_hint" not in str(result)


async def test_synthetic_tool_result_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le tool_result synthétique B3 ne doit pas être mis en cache
    (sinon on empoisonnerait le cache avec une erreur transitoire)."""

    async def _fake_invoke(name: str, args: dict) -> dict:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "crédits insuffisants"})}],
            "isError": False,
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _fake_invoke)

    await call_tool("comptes-entreprise", {"siren": "775670417", "annee": "2024"})

    # Le cache n'a rien stocké pour cette clé.
    cached = await cache.get("comptes-entreprise", {"siren": "775670417", "annee": "2024"})
    assert cached is None
