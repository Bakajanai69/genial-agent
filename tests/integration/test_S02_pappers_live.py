"""Tests d'intégration live contre le MCP Pappers.

**Skip explicite** si `PAPPERS_API_KEY` n'est pas dans l'environnement
(message visible dans la sortie pytest). Consomme des crédits Pappers
réels → solde ≥ 50 recommandé avant exécution.
"""

from __future__ import annotations

import os
import time

import pytest

from genial_agent import mcp_pappers

pytestmark = pytest.mark.integration

SKIP_REASON = "PAPPERS_API_KEY not set"


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_healthcheck_live() -> None:
    result = await mcp_pappers.healthcheck()
    assert result["status"] == "ok", result
    assert result["tools_count"] > 0


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_tools_discovery_includes_retained_core() -> None:
    """Le probe réel du 2026-04-24 montre que `sirenisateur` et
    `informations-entreprise` sont exposés — s'ils disparaissent,
    la démo U1 casse."""
    tools = await mcp_pappers.list_available_tools()
    names = {t.name for t in tools}
    assert "sirenisateur" in names
    assert "informations-entreprise" in names


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_call_tool_lvmh_then_cache_hit() -> None:
    """Deux `call_tool` identiques rapprochés → le 2ᵉ est servi par le
    cache (vérifiable par l'invariant temporel : 2ᵉ appel < 50 ms)."""
    args = {"siren": "775670417"}  # LVMH

    # Clean cache entry pour garantir un vrai 1er appel réseau.
    key = mcp_pappers.cache.key("informations-entreprise", args)
    mcp_pappers.cache._store.pop(key, None)  # noqa: SLF001 — accès interne légitime

    t0 = time.monotonic()
    r1 = await mcp_pappers.call_tool("informations-entreprise", args)
    t1 = time.monotonic()
    r2 = await mcp_pappers.call_tool("informations-entreprise", args)
    t2 = time.monotonic()

    assert r1 == r2
    assert (t2 - t1) < 0.05, "2e appel aurait dû être un cache hit instantané"
    # Le 1er appel doit être lent par rapport au 2e (sanity check).
    assert (t1 - t0) > (t2 - t1)


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_anthropic_schema_payload_is_usable() -> None:
    """Sanity : le payload produit est bien accepté comme `tools`
    param par la validation côté anthropic TypedDict (clés ok)."""
    tools = await mcp_pappers.list_available_tools()
    schema = mcp_pappers.to_anthropic_schema(tools)
    assert schema, "aucun tool retenu → bug de discovery ou filtrage"
    for t in schema:
        assert set(t.keys()) == {"name", "description", "input_schema"}
        assert isinstance(t["input_schema"], dict)
        assert t["input_schema"].get("type") == "object"
