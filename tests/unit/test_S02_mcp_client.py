"""Tests unitaires du client MCP Pappers : URL, retry predicate, garde clé,
single-flight, healthcheck contract, prewarm_cache.

Les fixtures ``_restore_settings`` et ``_fresh_cache`` (cf.
``tests/conftest.py``) isolent chaque test : plus de mutation permanente
de ``settings`` ni de fuite du singleton ``cache``.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import anyio
import httpx
import pytest
from mcp.shared.exceptions import McpError

from genial_agent import mcp_pappers

# ── Helpers ──────────────────────────────────────────────────────────


def _set_key(monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    """Installe ``PAPPERS_API_KEY=key`` sur le singleton ``settings`` en
    créant un nouveau dataclass (``replace``). ``monkeypatch`` restaure
    automatiquement la valeur d'origine à la fin du test."""
    new_settings = replace(mcp_pappers.settings, PAPPERS_API_KEY=key)
    monkeypatch.setattr(mcp_pappers, "settings", new_settings)


# ── URL construction ────────────────────────────────────────────────


def test_build_url_uses_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "abc123")
    assert mcp_pappers._build_url() == "https://mcp.pappers.fr/abc123"


def test_build_url_fails_if_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_key(monkeypatch, "")
    with pytest.raises(RuntimeError, match="PAPPERS_API_KEY"):
        mcp_pappers._build_url()


def test_api_key_never_in_module_string_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aucune constante str du module ne doit contenir la clé — garde-fou
    contre un leak par constante mal placée."""
    _set_key(monkeypatch, "SECRETKEY_X1")
    for attr_name in dir(mcp_pappers):
        if attr_name.startswith("_") or attr_name == "settings":
            continue
        val = getattr(mcp_pappers, attr_name)
        if isinstance(val, str):
            assert "SECRETKEY_X1" not in val


# ── RETAINED_TOOLS ──────────────────────────────────────────────────


def test_retained_tools_is_set() -> None:
    assert isinstance(mcp_pappers.RETAINED_TOOLS, set)
    # Baseline : sirenisateur + recherche-entreprises (non premium,
    # vérifiés live contre Pappers le 2026-04-24).
    assert {"sirenisateur", "recherche-entreprises"} <= mcp_pappers.RETAINED_TOOLS


def test_informations_entreprise_excluded_premium() -> None:
    """``informations-entreprise`` est Premium côté Pappers et renvoie
    systématiquement "crédits insuffisants" avec le pack offert. Retiré
    de ``RETAINED_TOOLS`` pour ne pas polluer le prompt agent."""
    assert "informations-entreprise" not in mcp_pappers.RETAINED_TOOLS


# ── _is_retryable ────────────────────────────────────────────────────


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


def test_is_retryable_mcp_and_anyio_errors() -> None:
    """Review C6 : erreurs MCP protocole + anyio stream broken +
    asyncio.TimeoutError doivent être retried (transient)."""
    from mcp.client.streamable_http import StreamableHTTPError
    from mcp.types import ErrorData

    # StreamableHTTPError
    assert mcp_pappers._is_retryable(StreamableHTTPError("proto")) is True
    # McpError (constructeur prend un ErrorData)
    mcp_err = McpError(ErrorData(code=-32000, message="transient"))
    assert mcp_pappers._is_retryable(mcp_err) is True
    # anyio stream errors
    assert mcp_pappers._is_retryable(anyio.EndOfStream()) is True
    assert mcp_pappers._is_retryable(anyio.BrokenResourceError()) is True
    # asyncio timeout (notre propre budget wall-clock)
    assert mcp_pappers._is_retryable(TimeoutError()) is True


# ── Exception hierarchy ──────────────────────────────────────────────


def test_exceptions_hierarchy() -> None:
    """Review M2 : ``CreditsExhausted`` et ``PappersToolError`` dérivent
    d'une base commune ``PappersError(RuntimeError)``."""
    assert issubclass(mcp_pappers.PappersError, RuntimeError)
    assert issubclass(mcp_pappers.CreditsExhausted, mcp_pappers.PappersError)
    assert issubclass(mcp_pappers.PappersToolError, mcp_pappers.PappersError)


# ── call_tool : cache / degraded / single-flight ─────────────────────


async def test_call_tool_cache_hit_skips_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si le cache contient déjà la réponse, on ne tape pas le réseau."""
    await mcp_pappers.cache.set("recherche-entreprises", {"q": "lvmh"}, {"cached": True})

    async def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("le réseau ne doit pas être appelé si le cache est chaud")

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _boom)
    res = await mcp_pappers.call_tool("recherche-entreprises", {"q": "lvmh"})
    assert res == {"cached": True}


async def test_call_tool_degraded_cache_miss_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """En mode dégradé, un cache miss lève ``CreditsExhausted``, jamais réseau."""
    monkeypatch.setattr(mcp_pappers, "_is_degraded", lambda: True)

    async def _boom(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("réseau interdit en mode dégradé")

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _boom)

    with pytest.raises(mcp_pappers.CreditsExhausted):
        await mcp_pappers.call_tool("recherche-entreprises", {"q": "nope"})


async def test_call_tool_concurrent_single_flight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review C3 : deux ``call_tool`` concurrents sur la même clé ne
    tapent le réseau qu'**une seule fois**. Scénario critique pour la
    conso de crédits en démo (3 onglets simultanés)."""
    calls = 0

    async def _slow_producer(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        # Laisse l'event loop passer la main — indispensable pour que le
        # 2ᵉ appelant arrive avant que le 1er ait posé son résultat.
        await asyncio.sleep(0.05)
        return {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _slow_producer)

    args = {"company_name": "LVMH", "country_code": "FR"}
    r1, r2 = await asyncio.gather(
        mcp_pappers.call_tool("sirenisateur", args),
        mcp_pappers.call_tool("sirenisateur", args),
    )
    assert r1 == r2
    assert calls == 1, f"single-flight failed: {calls} appels réseau au lieu de 1"


async def test_call_tool_concurrent_different_args_each_hits_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity : deux args différents → deux appels (on n'étouffe pas
    des requêtes distinctes)."""
    calls = 0

    async def _producer(_name: str, _args: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.01)
        return {"content": [{"type": "text", "text": '{"ok": true}'}], "isError": False}

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _producer)

    await asyncio.gather(
        mcp_pappers.call_tool("sirenisateur", {"company_name": "A", "country_code": "FR"}),
        mcp_pappers.call_tool("sirenisateur", {"company_name": "B", "country_code": "FR"}),
    )
    assert calls == 2


# ── _extract_business_error / _raise_business_error ──────────────────


def test_extract_business_error_credits_message() -> None:
    payload = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": (
                    '{"error": "Vous n\'avez pas de crédits suffisants pour exécuter cet outil."}'
                ),
            }
        ],
    }
    msg = mcp_pappers._extract_business_error(payload)
    assert msg is not None
    assert "crédits" in msg.lower()


def test_extract_business_error_respects_mcp_is_error_flag() -> None:
    payload = {
        "isError": True,
        "content": [{"type": "text", "text": "MCP error -32602: Invalid arguments"}],
    }
    msg = mcp_pappers._extract_business_error(payload)
    assert msg is not None
    assert "Invalid" in msg


def test_extract_business_error_returns_none_on_valid_payload() -> None:
    payload = {
        "isError": False,
        "content": [
            {
                "type": "text",
                "text": (
                    '{"possibilities": [{"company_number": "775670417", "company_name": "LVMH"}]}'
                ),
            }
        ],
    }
    assert mcp_pappers._extract_business_error(payload) is None


def test_extract_business_error_tolerates_non_json_text() -> None:
    payload = {
        "isError": False,
        "content": [{"type": "text", "text": "not a json blob"}],
    }
    assert mcp_pappers._extract_business_error(payload) is None


def test_extract_business_error_tolerates_empty_content() -> None:
    assert mcp_pappers._extract_business_error({"isError": False, "content": []}) is None


def test_extract_business_error_skips_non_text_leading_blocks() -> None:
    """Review R7 : si le 1er bloc MCP n'est pas 'text' (metadata,
    image…), on doit parcourir jusqu'au 1er bloc texte, pas rater
    l'erreur."""
    payload = {
        "isError": False,
        "content": [
            {"type": "resource", "uri": "pappers://meta"},
            {"type": "text", "text": '{"error": "Crédits insuffisants"}'},
        ],
    }
    msg = mcp_pappers._extract_business_error(payload)
    assert msg is not None
    assert "Crédits" in msg


def test_raise_business_error_credits_word_boundaries() -> None:
    """Review C2 : frontière de mot évite les faux positifs comme
    'discrédit' ou 'no credit card'. Seuls les vrais mots clés
    déclenchent ``CreditsExhausted``."""
    # Vrais crédits → CreditsExhausted
    with pytest.raises(mcp_pappers.CreditsExhausted):
        mcp_pappers._raise_business_error("t", "Vous n'avez pas de crédits suffisants")
    with pytest.raises(mcp_pappers.CreditsExhausted):
        mcp_pappers._raise_business_error("t", "Quota API dépassé")
    with pytest.raises(mcp_pappers.CreditsExhausted):
        mcp_pappers._raise_business_error("t", "Insufficient credit available")
    with pytest.raises(mcp_pappers.CreditsExhausted):
        mcp_pappers._raise_business_error("t", "tokens épuisés")
    # Faux positifs historiques → PappersToolError
    with pytest.raises(mcp_pappers.PappersToolError):
        mcp_pappers._raise_business_error("t", "personnalité accréditée non trouvée")
    with pytest.raises(mcp_pappers.PappersToolError):
        mcp_pappers._raise_business_error("t", "discrédit total sur la demande")
    with pytest.raises(mcp_pappers.PappersToolError):
        mcp_pappers._raise_business_error("t", "SIREN introuvable")


async def test_call_tool_credits_error_raises_and_does_not_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pappers renvoie HTTP 200 + isError=False + JSON {"error": "crédits…"} :
    call_tool doit lever ``CreditsExhausted`` **et ne rien cacher**, pour
    qu'un retour de crédits laisse un prochain appel repartir."""

    async def _returns_credits_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "isError": False,
            "content": [
                {
                    "type": "text",
                    "text": '{"error": "Vous n\'avez pas de crédits suffisants"}',
                }
            ],
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _returns_credits_error)
    args = {"siren": "credits-err"}

    with pytest.raises(mcp_pappers.CreditsExhausted):
        await mcp_pappers.call_tool("recherche-entreprises", args)

    assert await mcp_pappers.cache.get("recherche-entreprises", args) is None


async def test_call_tool_tool_error_raises_pappers_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Erreur métier hors crédits → ``PappersToolError``, pas de cache."""

    async def _returns_generic_error(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {
            "isError": False,
            "content": [{"type": "text", "text": '{"error": "SIREN introuvable"}'}],
        }

    monkeypatch.setattr(mcp_pappers, "_invoke_tool_live", _returns_generic_error)
    args = {"siren": "unknown"}

    with pytest.raises(mcp_pappers.PappersToolError) as exc_info:
        await mcp_pappers.call_tool("recherche-entreprises", args)
    assert "SIREN introuvable" in str(exc_info.value)
    assert await mcp_pappers.cache.get("recherche-entreprises", args) is None


# ── healthcheck contract (review B2) ─────────────────────────────────


async def test_healthcheck_ok_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review B2 : les 4 clés (status, latency_ms, tools_count, error)
    sont présentes dans la branche OK."""

    async def _list_ok() -> list[mcp_pappers.PappersTool]:
        return [mcp_pappers.PappersTool(name="sirenisateur", description="", input_schema={})]

    monkeypatch.setattr(mcp_pappers, "list_available_tools", _list_ok)
    res = await mcp_pappers.healthcheck()
    assert set(res.keys()) == {"status", "latency_ms", "tools_count", "error"}
    assert res["status"] == "ok"
    assert res["tools_count"] == 1
    assert res["error"] is None
    assert isinstance(res["latency_ms"], int)


async def test_healthcheck_ko_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review B2 (blocker) : la branche KO renvoie le **même** set de
    clés que la branche OK. Sans ça, un consumer S07 ``result["tools_count"]``
    lève ``KeyError`` sur échec."""

    async def _list_ko() -> list[mcp_pappers.PappersTool]:
        raise httpx.ConnectError("backend down")

    monkeypatch.setattr(mcp_pappers, "list_available_tools", _list_ko)
    res = await mcp_pappers.healthcheck()
    assert set(res.keys()) == {"status", "latency_ms", "tools_count", "error"}
    assert res["status"] == "ko"
    assert res["tools_count"] == 0
    assert res["error"] == "ConnectError"
    assert isinstance(res["latency_ms"], int)


# ── prewarm_cache (review R1) ────────────────────────────────────────


async def test_prewarm_cache_calls_four_golden_seeds() -> None:
    """Review S09.6 P1-6 : les 4 entités golden (LVMH, BNP, Carrefour,
    Casino Guichard) sont appelées via ``sirenisateur`` avec le schéma
    ``{company_name, country_code}``. Casino ajouté pour aligner sur
    ``prewarm_comptes_entreprise.py`` et garantir 0 crédit live sur le
    starter "Compare Carrefour vs Casino"."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def _fake_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append((name, args))
        return {"isError": False, "content": [{"type": "text", "text": '{"ok": true}'}]}

    await mcp_pappers.prewarm_cache(call=_fake_call)

    assert len(calls) == 4
    names_used = [c[0] for c in calls]
    assert all(n == "sirenisateur" for n in names_used)
    seeds = [c[1]["company_name"] for c in calls]
    assert seeds == ["LVMH", "BNP Paribas", "Carrefour", "Casino Guichard"]
    assert all(c[1]["country_code"] == "FR" for c in calls)


async def test_prewarm_cache_swallows_per_seed_errors() -> None:
    """Review R1 : une exception sur un seed n'empêche pas les autres —
    le préchauffage est best-effort, pas bloquant au démarrage."""
    calls: list[str] = []

    async def _flaky(_name: str, args: dict[str, Any]) -> dict[str, Any]:
        calls.append(args["company_name"])
        if args["company_name"] == "BNP Paribas":
            raise mcp_pappers.CreditsExhausted("credits")
        return {"isError": False, "content": []}

    # Doit terminer sans lever, et continuer après l'erreur sur BNP.
    await mcp_pappers.prewarm_cache(call=_flaky)
    assert calls == ["LVMH", "BNP Paribas", "Carrefour", "Casino Guichard"]


# ── _is_degraded memoization (review C4) ─────────────────────────────


def test_is_degraded_returns_false_without_credit_guard() -> None:
    """Tant que S07 n'a pas livré ``credit_guard``, ``_is_degraded``
    résout à ``False`` sans lever."""
    mcp_pappers._reset_degraded_cache()
    assert mcp_pappers._is_degraded() is False
    # 2ᵉ appel : mémoïsé, toujours False.
    assert mcp_pappers._is_degraded() is False


def test_is_degraded_memoizes_resolved_fn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review C4 : on ne doit pas re-résoudre ``_degraded_fn`` à chaque
    appel — une fois résolu, la fonction câblée est réutilisée."""
    mcp_pappers._reset_degraded_cache()
    # Après le 1er appel, le flag resolved doit être True.
    mcp_pappers._is_degraded()
    assert mcp_pappers._degraded_resolved is True


# ── Global budget on call_tool (review C1) ───────────────────────────


def test_call_tool_budget_constant_present() -> None:
    """Review C1 : le cap wall-clock est bien figé en constante (et
    passé à tenacity via ``stop_after_delay``)."""
    assert mcp_pappers.CALL_TOOL_BUDGET_S > 0
    assert mcp_pappers.CALL_TOOL_BUDGET_S <= 30  # cohérent avec cahier < 6 s UX
