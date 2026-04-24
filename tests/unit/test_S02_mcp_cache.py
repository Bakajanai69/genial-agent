"""Tests unitaires du cache tool-level : TTL, hash canonique, LRU bornée,
single-flight, tolérance d'args non-JSON natifs.

Couvre les corrections post-review S02 :
- C3 single-flight (coalescing concurrent cache miss).
- C5 éviction LRU bornée.
- C7 sérialisation d'args avec ``default=str``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from genial_agent.mcp_cache import ToolCache

# ── TTL + miss + canonicalisation ────────────────────────────────────


async def test_cache_hit() -> None:
    c = ToolCache(ttl_s=60)
    await c.set("get_company", {"siren": "775670417"}, {"name": "LVMH"})
    res = await c.get("get_company", {"siren": "775670417"})
    assert res == {"name": "LVMH"}


async def test_cache_miss_returns_none() -> None:
    c = ToolCache(ttl_s=60)
    assert await c.get("unknown", {"x": 1}) is None


async def test_cache_args_canonical_order() -> None:
    """Hash stable peu importe l'ordre des clés — sinon le cache rate."""
    c = ToolCache(ttl_s=60)
    await c.set("foo", {"a": 1, "b": 2}, {"ok": True})
    res = await c.get("foo", {"b": 2, "a": 1})
    assert res == {"ok": True}


async def test_cache_expires() -> None:
    c = ToolCache(ttl_s=0)
    await c.set("foo", {"x": 1}, {"v": 1})
    await asyncio.sleep(0.01)
    assert await c.get("foo", {"x": 1}) is None


async def test_cache_contains() -> None:
    c = ToolCache(ttl_s=60)
    assert await c.contains("foo", {"x": 1}) is False
    await c.set("foo", {"x": 1}, {"v": 1})
    assert await c.contains("foo", {"x": 1}) is True


def test_key_stable_and_sorted() -> None:
    assert ToolCache.key("foo", {"a": 1, "b": 2}) == ToolCache.key("foo", {"b": 2, "a": 1})
    # Tool name est un namespace — deux tools différents, même args → clés
    # différentes.
    assert ToolCache.key("foo", {"x": 1}) != ToolCache.key("bar", {"x": 1})


# ── LRU bounded size (review C5) ─────────────────────────────────────


async def test_cache_evicts_oldest_when_full() -> None:
    """Review C5 : au-delà de ``max_size``, l'entrée LRU est évincée.
    Protège la RAM sur Railway free tier."""
    c = ToolCache(ttl_s=60, max_size=2)
    await c.set("t", {"i": 1}, {"v": 1})
    await c.set("t", {"i": 2}, {"v": 2})
    # Trigger éviction : {i:1} est la plus ancienne.
    await c.set("t", {"i": 3}, {"v": 3})
    assert await c.get("t", {"i": 1}) is None
    assert await c.get("t", {"i": 2}) == {"v": 2}
    assert await c.get("t", {"i": 3}) == {"v": 3}


async def test_cache_lru_promotes_on_get() -> None:
    """Un ``get`` rend l'entrée 'fraîche' → elle n'est pas évincée au
    prochain ``set``."""
    c = ToolCache(ttl_s=60, max_size=2)
    await c.set("t", {"i": 1}, {"v": 1})
    await c.set("t", {"i": 2}, {"v": 2})
    # Touch i=1 → i=2 devient la plus ancienne.
    await c.get("t", {"i": 1})
    await c.set("t", {"i": 3}, {"v": 3})
    assert await c.get("t", {"i": 1}) == {"v": 1}
    assert await c.get("t", {"i": 2}) is None
    assert await c.get("t", {"i": 3}) == {"v": 3}


def test_max_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ToolCache(max_size=0)


# ── JSON-safe args (review C7) ───────────────────────────────────────


def test_key_tolerates_non_json_types() -> None:
    """Review C7 : ``datetime`` / ``set`` / ``Decimal`` ne doivent pas
    faire crasher la construction de clé — ``default=str`` convertit
    sans bruit."""
    # Avant C7, ça levait `TypeError: Object of type datetime is not JSON serializable`.
    k = ToolCache.key("t", {"when": datetime(2026, 4, 24)})
    assert isinstance(k, str) and k.startswith("t:")


# ── Single-flight (review C3) ────────────────────────────────────────


async def test_single_flight_coalesces_concurrent_producers() -> None:
    """Deux appelants sur la même clé partagent un seul producer. Impact
    crédits Pappers : 3 onglets simultanés = 1 crédit au lieu de 3."""
    c = ToolCache(ttl_s=60)
    calls = 0

    async def _producer() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"v": 42}

    r1, r2, r3 = await asyncio.gather(
        c.single_flight("t", {"x": 1}, _producer),
        c.single_flight("t", {"x": 1}, _producer),
        c.single_flight("t", {"x": 1}, _producer),
    )
    assert r1 == r2 == r3 == {"v": 42}
    assert calls == 1


async def test_single_flight_distinct_keys_are_independent() -> None:
    c = ToolCache(ttl_s=60)
    calls = 0

    async def _producer_factory(val: int):
        async def _p() -> dict[str, int]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"v": val}

        return _p

    r1, r2 = await asyncio.gather(
        c.single_flight("t", {"x": 1}, await _producer_factory(1)),
        c.single_flight("t", {"x": 2}, await _producer_factory(2)),
    )
    assert r1 == {"v": 1}
    assert r2 == {"v": 2}
    assert calls == 2


async def test_single_flight_propagates_exception_to_all_waiters() -> None:
    """Si le leader plante, tous les waiters reçoivent la même exception —
    personne ne reste bloqué ou n'obtient un résultat fantôme."""
    c = ToolCache(ttl_s=60)

    async def _bomb() -> dict[str, int]:
        await asyncio.sleep(0.02)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.gather(
            c.single_flight("t", {"x": 1}, _bomb),
            c.single_flight("t", {"x": 1}, _bomb),
        )

    # Et surtout, l'entrée inflight doit avoir été libérée pour que le
    # prochain appelant reparte sur un nouveau producer (pas collé à
    # l'erreur précédente).
    async def _ok() -> dict[str, int]:
        return {"v": 1}

    res = await c.single_flight("t", {"x": 1}, _ok)
    assert res == {"v": 1}


# ── clear() helper for tests ─────────────────────────────────────────


async def test_cache_clear_empties_store() -> None:
    c = ToolCache(ttl_s=60)
    await c.set("t", {"x": 1}, {"v": 1})
    c.clear()
    assert await c.get("t", {"x": 1}) is None
