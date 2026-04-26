"""S09.7 Axe 3 C5 — Anthropic prompt caching activation.

Tests **structurels** (pas d'appel live API). Vérifient que :

- ``agent.run_turn`` pose ``cache_control: {"type": "ephemeral"}`` sur :
  - le **dernier tool** du préfixe stable (avant ``extra_tools``
    dynamiques comme ``escalate_to_sonnet``),
  - le **bloc system** (converti en liste de blocs avec cache_control),
  - le **dernier content block** du **dernier message** de l'historique.
- L'event ``llm_meta`` forwarde ``cache_creation_tokens`` et
  ``cache_read_tokens``.
- L'agrégation pipeline incrémente les nouveaux compteurs stats
  ``anthropic_cache_creation_tokens`` / ``anthropic_cache_read_tokens``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from genial_agent.agent import ConversationState, run_turn
from genial_agent.models import ModelTier


@dataclass
class _FakeUsage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _FakeFinal:
    content: list = None  # type: ignore[assignment]
    stop_reason: str = "end_turn"
    model: str = "claude-haiku-4-5-20251001"
    usage: _FakeUsage = None  # type: ignore[assignment]


def _build_fake_client(
    final: _FakeFinal,
    captured_kwargs: dict[str, Any],
) -> AsyncMock:
    """Construit un AsyncAnthropic mock qui capture ``stream(**kwargs)``
    pour qu'on puisse inspecter ``system`` / ``tools`` / ``messages``.

    On laisse le ``async for`` du text_stream vide (pas de delta) et on
    retourne ``final`` dès ``get_final_message()``.
    """
    mock_client = MagicMock()

    @asynccontextmanager
    async def _client_ctx(*args: Any, **kwargs: Any):  # noqa: ARG001
        yield mock_client

    @asynccontextmanager
    async def _stream(**kwargs: Any):
        captured_kwargs.update(kwargs)
        stream = MagicMock()

        async def _empty_iter():
            return
            yield  # pragma: no cover — no-op generator

        stream.text_stream = _empty_iter()
        stream.get_final_message = AsyncMock(return_value=final)
        stream.request_id = "req_test_123"
        yield stream

    mock_client.messages.stream = _stream

    factory = MagicMock(side_effect=_client_ctx)
    factory.return_value = _client_ctx()
    return factory


@pytest.fixture
def patched_anthropic(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Patch ``AsyncAnthropic`` dans ``agent`` pour ne pas appeler le réseau,
    et patch ``mcp_pappers.list_available_tools`` pour ne pas hit le MCP."""
    from genial_agent import agent as agent_mod

    final = _FakeFinal(
        content=[],
        stop_reason="end_turn",
        usage=_FakeUsage(
            input_tokens=1234,
            output_tokens=56,
            cache_creation_input_tokens=789,
            cache_read_input_tokens=1011,
        ),
    )
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        agent_mod,
        "AsyncAnthropic",
        _build_fake_client(final, captured),
    )

    async def _no_tools() -> list:
        return []

    monkeypatch.setattr(agent_mod.mcp_pappers, "list_available_tools", _no_tools)
    return {"captured": captured, "final": final}


async def test_cache_control_on_last_tool_of_stable_prefix(
    patched_anthropic: dict[str, Any],
) -> None:
    """Le **dernier tool** du préfixe stable (Pappers + LOCAL_PAYLOAD_TOOLS)
    porte ``cache_control: ephemeral``. Si ``extra_tools`` est ajouté
    après (cas Haiku avec escalate_to_sonnet), il NE doit PAS être
    cache_control'd (sinon il invalide le cache à chaque routing
    decision).
    """
    state = ConversationState()
    extra = [{"name": "escalate_to_sonnet", "description": "x", "input_schema": {"type": "object"}}]

    async for _ in run_turn(state, "Hello", tier=ModelTier.HAIKU, extra_tools=extra):
        pass

    captured = patched_anthropic["captured"]
    tools_schema: list[dict[str, Any]] = captured["tools"]
    # Le dernier tool est forcément escalate_to_sonnet (extra ajouté en queue)
    assert tools_schema[-1]["name"] == "escalate_to_sonnet"
    assert "cache_control" not in tools_schema[-1]
    # Le 1er tool du préfixe stable (vraiment stable) doit exister et
    # **un** tool quelque part dans tools[:-1] doit porter cache_control.
    has_cache = any("cache_control" in t for t in tools_schema[:-1])
    assert has_cache, f"aucun cache_control sur le préfixe stable : {tools_schema}"


async def test_system_is_a_list_with_cache_control(
    patched_anthropic: dict[str, Any],
) -> None:
    """``system`` est passé à l'API Anthropic sous forme d'une liste de
    blocs avec ``cache_control: ephemeral`` (pas une simple string)."""
    state = ConversationState()
    async for _ in run_turn(state, "Hello", tier=ModelTier.HAIKU):
        pass
    system = patched_anthropic["captured"]["system"]
    assert isinstance(system, list)
    assert len(system) >= 1
    last_block = system[-1]
    assert last_block.get("type") == "text"
    assert last_block.get("cache_control") == {"type": "ephemeral"}


async def test_messages_passed_through_unchanged(
    patched_anthropic: dict[str, Any],
) -> None:
    """``messages`` est forwardé tel quel à l'API Anthropic, **sans**
    cache_control posé en dynamique sur ``messages[-1]``.

    Régression S09.7 phase 2 (run G1 live 2026-04-26) : poser
    ``cache_control`` sur un block ``tool_result`` du dernier message
    fait chuter ``input_tokens`` à 2 et l'agent boucle sur le même
    tool sans "voir" les résultats. Garder uniquement les 2
    breakpoints stables (tools + system) suffit à obtenir un
    cache_read substantiel au tour N+1 sans casser la visibilité
    du contexte conversation.
    """
    state = ConversationState()
    async for _ in run_turn(state, "Hello world", tier=ModelTier.HAIKU):
        pass
    messages = patched_anthropic["captured"]["messages"]
    last_msg = messages[-1]
    content = last_msg["content"]
    # Si content est une string, on la passe brute (pas de transformation
    # bloc-cache_control). Si liste, aucun bloc ne doit porter
    # cache_control (c'est l'objet du test : pas de breakpoint dynamique).
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                assert "cache_control" not in block


async def test_llm_meta_forwards_cache_token_counts(
    patched_anthropic: dict[str, Any],
) -> None:
    """L'event ``llm_meta`` expose ``cache_creation_tokens`` et
    ``cache_read_tokens`` (les deux compteurs Anthropic 2026)."""
    state = ConversationState()
    events = []
    async for ev in run_turn(state, "Hello", tier=ModelTier.HAIKU):
        events.append(ev)
    llm_metas = [e for e in events if e.get("type") == "llm_meta"]
    assert llm_metas, f"pas d'event llm_meta dans {[e.get('type') for e in events]}"
    meta = llm_metas[0]
    assert meta["cache_creation_tokens"] == 789
    assert meta["cache_read_tokens"] == 1011


async def test_llm_meta_cache_tokens_default_to_zero_if_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si l'API renvoie un usage sans ``cache_*_input_tokens`` (ancien SDK
    ou réponse sans cache), les compteurs défaultent à 0 sans crash."""
    from genial_agent import agent as agent_mod

    @dataclass
    class _BareUsage:
        input_tokens: int = 1
        output_tokens: int = 1
        # pas de cache_creation_input_tokens / cache_read_input_tokens

    final = _FakeFinal(
        content=[],
        stop_reason="end_turn",
        usage=_BareUsage(),
    )
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        agent_mod,
        "AsyncAnthropic",
        _build_fake_client(final, captured),
    )

    async def _no_tools() -> list:
        return []

    monkeypatch.setattr(agent_mod.mcp_pappers, "list_available_tools", _no_tools)

    state = ConversationState()
    metas = []
    async for ev in run_turn(state, "Hello", tier=ModelTier.HAIKU):
        if ev.get("type") == "llm_meta":
            metas.append(ev)
    assert metas[0]["cache_creation_tokens"] == 0
    assert metas[0]["cache_read_tokens"] == 0
