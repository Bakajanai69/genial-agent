"""Tests unitaires du client MCP Pappers : URL, retry predicate, garde clé."""

from __future__ import annotations

import httpx
import pytest

from genial_agent import mcp_pappers


def test_build_url_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # `settings` est un dataclass frozen : on patche le champ via setattr sur
    # l'objet importé par le module (object.__setattr__ bypass le frozen).
    object.__setattr__(mcp_pappers.settings, "PAPPERS_API_KEY", "abc123")
    url = mcp_pappers._build_url()
    assert url == "https://mcp.pappers.fr/abc123"


def test_build_url_fails_if_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    object.__setattr__(mcp_pappers.settings, "PAPPERS_API_KEY", "")
    with pytest.raises(RuntimeError, match="PAPPERS_API_KEY"):
        mcp_pappers._build_url()


def test_retained_tools_is_set() -> None:
    assert isinstance(mcp_pappers.RETAINED_TOOLS, set)
    assert {"sirenisateur", "informations-entreprise"} <= mcp_pappers.RETAINED_TOOLS


def test_is_retryable_401_403_404_not_retried() -> None:
    for code in (401, 403, 404):
        resp = httpx.Response(code, request=httpx.Request("GET", "http://x"))
        err = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
        assert mcp_pappers._is_retryable(err) is False


def test_is_retryable_429_and_5xx_are_retried() -> None:
    for code in (429, 500, 502, 503, 504):
        resp = httpx.Response(code, request=httpx.Request("GET", "http://x"))
        err = httpx.HTTPStatusError("boom", request=resp.request, response=resp)
        assert mcp_pappers._is_retryable(err) is True


def test_is_retryable_transport_and_timeout() -> None:
    assert mcp_pappers._is_retryable(httpx.ConnectError("down")) is True
    assert (
        mcp_pappers._is_retryable(
            httpx.ReadTimeout("slow", request=httpx.Request("GET", "http://x"))
        )
        is True
    )
    # Exception non liée → pas de retry
    assert mcp_pappers._is_retryable(ValueError("nope")) is False


def test_credits_exhausted_is_runtime_error() -> None:
    """Sanity : S03/S07 attrapent `CreditsExhausted` parmi les RuntimeError."""
    assert issubclass(mcp_pappers.CreditsExhausted, RuntimeError)


def test_api_key_never_in_module_string_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aucune constante str du module ne doit contenir la clé — garde-fou
    contre un leak par constante mal placée. On ne teste PAS `settings`
    lui-même (dataclass standard expose la clé par design)."""
    object.__setattr__(mcp_pappers.settings, "PAPPERS_API_KEY", "SECRETKEY_X1")
    for attr_name in dir(mcp_pappers):
        if attr_name.startswith("_") or attr_name == "settings":
            continue
        val = getattr(mcp_pappers, attr_name)
        if isinstance(val, str):
            assert "SECRETKEY_X1" not in val


async def test_call_tool_cache_hit_skips_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si le cache contient déjà la réponse, on ne tape pas le réseau."""
    # Prime le cache avec un résultat factice.
    await mcp_pappers.cache.set("informations-entreprise", {"siren": "000"}, {"cached": True})

    async def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("le réseau ne doit pas être appelé si le cache est chaud")

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _boom)
    res = await mcp_pappers.call_tool("informations-entreprise", {"siren": "000"})
    assert res == {"cached": True}


async def test_call_tool_degraded_cache_miss_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En mode dégradé, un cache miss lève `CreditsExhausted`, jamais réseau."""
    monkeypatch.setattr(mcp_pappers, "_is_degraded", lambda: True)

    async def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("réseau interdit en mode dégradé")

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _boom)

    # Args uniques pour éviter les résidus cache d'autres tests.
    with pytest.raises(mcp_pappers.CreditsExhausted):
        await mcp_pappers.call_tool("informations-entreprise", {"siren": "deadbeef-degraded"})
