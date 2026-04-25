"""Tests de l'``IdempotenceCache`` (S07 §"Idempotence : portée et durée").

Couvre TTL, isolation par session_id, LRU eviction, robustesse Unicode.
"""

from __future__ import annotations

import asyncio

import pytest

from genial_agent.observability.idempotence import IdempotenceCache


async def test_hit_within_ttl() -> None:
    c = IdempotenceCache(ttl_s=10)
    await c.set("sess1", "hello", "response A")
    assert await c.get("sess1", "hello") == "response A"


async def test_miss_different_session() -> None:
    c = IdempotenceCache(ttl_s=10)
    await c.set("sess1", "hello", "A")
    assert await c.get("sess2", "hello") is None


async def test_miss_different_message() -> None:
    c = IdempotenceCache(ttl_s=10)
    await c.set("sess1", "hello", "A")
    assert await c.get("sess1", "world") is None


async def test_expires_after_ttl() -> None:
    c = IdempotenceCache(ttl_s=0)  # expire immédiatement
    await c.set("sess1", "hello", "A")
    await asyncio.sleep(0.01)
    assert await c.get("sess1", "hello") is None


async def test_lru_eviction_when_full() -> None:
    c = IdempotenceCache(ttl_s=60, max_entries=2)
    await c.set("s", "a", "A")
    await c.set("s", "b", "B")
    await c.set("s", "c", "C")  # éviction de "a"
    assert await c.get("s", "a") is None
    assert await c.get("s", "b") == "B"
    assert await c.get("s", "c") == "C"


async def test_unicode_message_doesnt_crash() -> None:
    c = IdempotenceCache(ttl_s=10)
    msg = "fiche 🇫🇷 LVMH é€ 中文"
    await c.set("s", msg, "ok")
    assert await c.get("s", msg) == "ok"


async def test_clear_empties_cache() -> None:
    c = IdempotenceCache(ttl_s=60)
    await c.set("s", "a", "A")
    await c.clear()
    assert await c.get("s", "a") is None


async def test_set_overwrites_existing_entry() -> None:
    c = IdempotenceCache(ttl_s=60)
    await c.set("s", "a", "first")
    await c.set("s", "a", "second")
    assert await c.get("s", "a") == "second"


def test_max_entries_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        IdempotenceCache(max_entries=0)


def test_key_isolation_per_session() -> None:
    """Vérifie que ``key`` produit des hashes différents par session_id
    pour le même message — sinon on aurait fuite cross-session."""
    k1 = IdempotenceCache.key("sess_a", "fiche LVMH")
    k2 = IdempotenceCache.key("sess_b", "fiche LVMH")
    assert k1 != k2
    # Même session + même message = même clé (idempotence).
    assert IdempotenceCache.key("sess_a", "fiche LVMH") == k1
