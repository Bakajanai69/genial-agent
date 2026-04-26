"""Tests S09.6 — TTL différencié par tool (axe D2).

Politique :

- ``DEFAULT_TTL_S`` = 24h pour les tools "live" (sirenisateur,
  recherche-entreprises, recherche-dirigeants, conformite-…).
- ``TOOL_TTL_OVERRIDES`` 7j pour les tools "snapshots" annuels
  (comptes-entreprise, cartographie-entreprise) — données peu volatiles,
  lourds en crédits, justifient un cache long pour absorber les fenêtres
  de blocage abo / bug PAYG.
- Tool inconnu → fallback sur ``DEFAULT_TTL_S``.
"""

from __future__ import annotations

import time

import pytest

from genial_agent.mcp_cache import DEFAULT_TTL_S, TOOL_TTL_OVERRIDES, ToolCache


def test_default_ttl_is_24h() -> None:
    assert DEFAULT_TTL_S == 24 * 3600


def test_overrides_contain_expected_tools() -> None:
    """Le mapping doit cibler exactement les 2 tools "snapshots"
    identifiés en phase 1 (matrice S09.6 §"Logique de cache MCP")."""
    assert TOOL_TTL_OVERRIDES["comptes-entreprise"] == 7 * 24 * 3600
    assert TOOL_TTL_OVERRIDES["cartographie-entreprise"] == 7 * 24 * 3600
    # Pas d'override pour les tools "live".
    assert "sirenisateur" not in TOOL_TTL_OVERRIDES
    assert "recherche-entreprises" not in TOOL_TTL_OVERRIDES
    assert "recherche-dirigeants" not in TOOL_TTL_OVERRIDES


@pytest.mark.parametrize(
    "tool_name, expected_ttl",
    [
        ("sirenisateur", 24 * 3600),
        ("recherche-entreprises", 24 * 3600),
        ("recherche-dirigeants", 24 * 3600),
        ("conformite-personne-physique", 24 * 3600),
        ("comptes-entreprise", 7 * 24 * 3600),
        ("cartographie-entreprise", 7 * 24 * 3600),
        # Tool inconnu (futur ou hors scope retenus) → fallback default.
        ("unknown-tool", 24 * 3600),
    ],
)
async def test_set_uses_correct_ttl_per_tool(
    tool_name: str, expected_ttl: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``set()`` applique le TTL spécifique au tool. On freeze ``time.time``
    pour vérifier ``expires_at`` au float près."""
    fixed_now = 1_000_000.0
    monkeypatch.setattr("genial_agent.mcp_cache.time.time", lambda: fixed_now)

    c = ToolCache(ttl_s=DEFAULT_TTL_S)
    await c.set(tool_name, {"x": 1}, {"v": 1})

    k = ToolCache.key(tool_name, {"x": 1})
    entry = c._store[k]
    assert entry.expires_at == pytest.approx(fixed_now + expected_ttl)


async def test_short_tool_expires_before_long_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un tool "live" (24h) doit expirer avant un tool "snapshot" (7j)
    quand on avance le temps de 25 h."""
    fake_time = 1_000_000.0

    def _now() -> float:
        return fake_time

    monkeypatch.setattr("genial_agent.mcp_cache.time.time", _now)

    c = ToolCache()
    await c.set("sirenisateur", {"siren": "1"}, {"v": "live"})
    await c.set("comptes-entreprise", {"siren": "1", "annee": 2024}, {"v": "snap"})

    # Avance de 25h : sirenisateur (TTL 24h) expiré, comptes-entreprise (7j) frais.
    fake_time = 1_000_000.0 + 25 * 3600
    assert await c.get("sirenisateur", {"siren": "1"}) is None
    assert await c.get("comptes-entreprise", {"siren": "1", "annee": 2024}) == {"v": "snap"}


async def test_long_tool_expires_after_seven_days(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un tool "snapshot" expire bien à 7j + 1s, pas avant."""
    fake_time = 1_000_000.0

    def _now() -> float:
        return fake_time

    monkeypatch.setattr("genial_agent.mcp_cache.time.time", _now)

    c = ToolCache()
    await c.set("comptes-entreprise", {"siren": "1", "annee": 2024}, {"v": "snap"})

    # 7j - 1s : encore frais.
    fake_time = 1_000_000.0 + 7 * 24 * 3600 - 1
    assert await c.get("comptes-entreprise", {"siren": "1", "annee": 2024}) == {"v": "snap"}

    # 7j + 1s : expiré.
    fake_time = 1_000_000.0 + 7 * 24 * 3600 + 1
    assert await c.get("comptes-entreprise", {"siren": "1", "annee": 2024}) is None


async def test_override_persisted_to_disk_round_trip(tmp_path) -> None:
    """L'``expires_at`` calculé avec un override TTL doit survivre au
    round-trip disque (le fichier persistant écrit l'expires_at epoch,
    pas un delta TTL)."""
    persist = tmp_path / "cache.json"
    c1 = ToolCache(persist_path=persist)
    before = time.time()
    await c1.set("comptes-entreprise", {"siren": "1", "annee": 2024}, {"v": "snap"})
    k = ToolCache.key("comptes-entreprise", {"siren": "1", "annee": 2024})
    expires = c1._store[k].expires_at

    # Doit être ~7j dans le futur (assertion grossière à ±5s pour absorber
    # la latence du test).
    assert expires - before == pytest.approx(7 * 24 * 3600, abs=5)

    # Reload et vérification que l'expiration est conservée.
    c2 = ToolCache(persist_path=persist)
    assert c2._store[k].expires_at == pytest.approx(expires, abs=0.001)
