"""Tests unitaires du cache tool-level (TTL 24 h, hash canonique)."""

from __future__ import annotations

import asyncio

from genial_agent.mcp_cache import ToolCache


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
