"""Tests d'intégration live contre le MCP Pappers.

**Skip explicite** si `PAPPERS_API_KEY` n'est pas dans l'environnement
(message visible dans la sortie pytest). Consomme des crédits Pappers
réels → solde ≥ 10 recommandé avant exécution (chaque run fait ~2 calls
facturables : 1× sirenisateur + le 2ᵉ servi par cache).
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
    """Le probe réel du 2026-04-24 montre que ``sirenisateur`` et
    ``recherche-entreprises`` sont exposés et non-premium — s'ils
    disparaissent, la démo U1/U3 casse."""
    tools = await mcp_pappers.list_available_tools()
    names = {t.name for t in tools}
    assert "sirenisateur" in names
    assert "recherche-entreprises" in names


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_sirenisateur_lvmh_returns_real_data_then_cache_hit() -> None:
    """Deux ``call_tool`` identiques rapprochés sur ``sirenisateur`` :
    le 1er consomme 1 crédit et ramène la vraie fiche LVMH, le 2ᵉ est
    servi par le cache (invariant temporel : 2ᵉ < 50 ms).

    Params validés contre l'``inputSchema`` du probe 2026-04-24 :
    ``company_name`` + ``country_code`` (FR).
    """
    args = {"company_name": "LVMH", "country_code": "FR"}

    # Clean cache entry pour garantir un vrai 1er appel réseau.
    key = mcp_pappers.cache.key("sirenisateur", args)
    mcp_pappers.cache._store.pop(key, None)  # noqa: SLF001 — accès interne légitime

    t0 = time.monotonic()
    r1 = await mcp_pappers.call_tool("sirenisateur", args)
    t1 = time.monotonic()
    r2 = await mcp_pappers.call_tool("sirenisateur", args)
    t2 = time.monotonic()

    assert r1 == r2
    assert (t2 - t1) < 0.05, "2e appel aurait dû être un cache hit instantané"
    assert (t1 - t0) > (t2 - t1)

    # Sanity : la réponse contient bien le SIREN LVMH (775670417).
    content = r1.get("content") or []
    assert content, "réponse MCP sans content"
    text = content[0].get("text", "")
    assert "775670417" in text, f"SIREN LVMH absent de la réponse sirenisateur : {text[:300]!r}"


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_informations_entreprise_premium_raises_credits_exhausted() -> None:
    """``informations-entreprise`` est Premium chez Pappers : avec le
    pack API offert (100 crédits), il renvoie systématiquement l'erreur
    textuelle JSON ``{"error": "crédits insuffisants..."}``.

    On vérifie :
    - ``call_tool`` lève ``CreditsExhausted`` (pas un succès silencieux).
    - Le cache reste vide (on ne mémorise pas une erreur).

    Note : ce test passe même si un jour le plan du compte change et
    l'outil devient payant — il ne fait que documenter le garde-fou.
    Si l'outil devient servi, le test échoue et on le met à jour.
    """
    args = {"siren": "775670417"}  # LVMH
    key = mcp_pappers.cache.key("informations-entreprise", args)
    mcp_pappers.cache._store.pop(key, None)  # noqa: SLF001

    with pytest.raises((mcp_pappers.CreditsExhausted, mcp_pappers.PappersToolError)):
        await mcp_pappers.call_tool("informations-entreprise", args)

    # Garde-fou : l'erreur n'a pas pollué le cache.
    assert await mcp_pappers.cache.get("informations-entreprise", args) is None


@pytest.mark.skipif(not os.getenv("PAPPERS_API_KEY"), reason=SKIP_REASON)
async def test_anthropic_schema_payload_is_usable() -> None:
    """Sanity : le payload produit est bien accepté comme ``tools``
    param par la validation côté anthropic TypedDict (clés ok)."""
    tools = await mcp_pappers.list_available_tools()
    schema = mcp_pappers.to_anthropic_schema(tools)
    assert schema, "aucun tool retenu → bug de discovery ou filtrage"
    for t in schema:
        assert set(t.keys()) == {"name", "description", "input_schema"}
        assert isinstance(t["input_schema"], dict)
        assert t["input_schema"].get("type") == "object"
