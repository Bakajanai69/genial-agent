"""Tests S10 — voice/openai_adapter : SSE format + conversion + cancellation."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import patch

import pytest

from genial_agent.observability import stats as stats_module
from genial_agent.voice import openai_adapter
from genial_agent.voice.openai_adapter import (
    _convert_history_to_anthropic,
    _sse_chunk,
    _sse_done,
    _stream_chat_completion,
)

# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_sse_done_format() -> None:
    assert _sse_done() == "data: [DONE]\n\n"


def test_sse_chunk_role_first() -> None:
    chunk = _sse_chunk("", chunk_id="cid", model_label="m", role="assistant")
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")
    payload = json.loads(chunk[len("data: ") :].strip())
    assert payload["choices"][0]["delta"] == {"role": "assistant"}
    assert payload["object"] == "chat.completion.chunk"
    assert payload["model"] == "m"


def test_sse_chunk_content_only() -> None:
    chunk = _sse_chunk("Bonjour", chunk_id="cid", model_label="m")
    payload = json.loads(chunk[len("data: ") :].strip())
    assert payload["choices"][0]["delta"] == {"content": "Bonjour"}
    assert payload["choices"][0]["finish_reason"] is None


def test_sse_chunk_finish_reason() -> None:
    chunk = _sse_chunk("", chunk_id="cid", model_label="m", finish_reason="stop")
    payload = json.loads(chunk[len("data: ") :].strip())
    assert payload["choices"][0]["finish_reason"] == "stop"


# --------------------------------------------------------------------------- #
# History conversion
# --------------------------------------------------------------------------- #


def test_convert_history_extracts_last_user() -> None:
    msgs = [
        {"role": "system", "content": "ignored"},
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2"},
    ]
    history, last = _convert_history_to_anthropic(msgs)
    assert last == "Q2"
    # System ignoré + dernier user retiré → 2 messages d'historique.
    assert history == [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
    ]


def test_convert_history_empty() -> None:
    history, last = _convert_history_to_anthropic([])
    assert history == []
    assert last == ""


def test_convert_history_only_system_returns_empty() -> None:
    history, last = _convert_history_to_anthropic([{"role": "system", "content": "ignored"}])
    assert history == []
    assert last == ""


def test_convert_history_non_string_content_stringified() -> None:
    msgs = [
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {"role": "user", "content": "final"},
    ]
    history, last = _convert_history_to_anthropic(msgs)
    assert last == "final"
    # Le 1er user est stringified (json) — pas de crash.
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert "hello" in history[0]["content"]


# --------------------------------------------------------------------------- #
# Streaming end-to-end (mock run_guarded_turn)
# --------------------------------------------------------------------------- #


class _FakeRequest:
    """Stand-in pour starlette.Request avec ``is_disconnected``."""

    def __init__(self, disconnect_after: int | None = None) -> None:
        self._calls = 0
        self._disconnect_after = disconnect_after

    async def is_disconnected(self) -> bool:
        if self._disconnect_after is None:
            return False
        self._calls += 1
        return self._calls > self._disconnect_after


def _events(*evs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    async def _gen() -> AsyncIterator[dict[str, Any]]:
        for e in evs:
            yield e

    return _gen()


def _strs(*deltas: str) -> AsyncIterator[str]:
    """Stand-in pour le reformulator stream — yield des deltas string."""

    async def _gen() -> AsyncIterator[str]:
        for d in deltas:
            yield d

    return _gen()


def _drain_chunks(stream: AsyncIterator[str]) -> list[str]:
    """Drain l'async iterator → list[str] (run synchrone via asyncio)."""
    import asyncio

    async def _run() -> list[str]:
        return [c async for c in stream]

    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def _fresh_stats() -> None:
    stats_module.reset_for_tests()
    yield
    stats_module.reset_for_tests()


@pytest.fixture(autouse=True)
def _mock_reformulator(request: pytest.FixtureRequest):
    """Auto-mock le reformulateur Haiku par défaut pour TOUS les tests
    (évite les vrais appels Anthropic pendant les tests unit). Tests
    qui veulent un comportement custom peuvent override avec un
    ``patch.object(openai_adapter, "reformulate_for_voice_stream", ...)``.
    """
    if "no_reformulator_mock" in request.keywords:
        yield
        return
    with patch.object(
        openai_adapter,
        "reformulate_for_voice_stream",
        side_effect=lambda question, text, **kw: _strs(f"[REFORMULATED] {text}"),
    ):
        yield


def test_stream_emits_role_first_then_reformulated_then_done() -> None:
    """Pipeline 2-passes : main LLM bufferé, reformulator yield SSE,
    [DONE] en fin. Le main text seul n'est PAS yieldé direct.

    Utilise du Markdown pour bypass le skip B2 et forcer le reformulator.
    """
    body = {"messages": [{"role": "user", "content": "Donne-moi LVMH"}], "stream": True}
    fake_req = _FakeRequest()

    fake_events = _events(
        {"type": "text", "content": "**Bonjour**, "},  # Markdown bold = no-skip
        {"type": "text", "content": "voici la réponse."},
        {"type": "end", "reason": "end_turn"},
    )

    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        chunks = _drain_chunks(_stream_chat_completion(body, fake_req))

    # Premier chunk : delta.role = assistant.
    first = json.loads(chunks[0][len("data: ") :].strip())
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    # Le reformulateur (mocké) yield un chunk préfixé "[REFORMULATED] ...".
    body_chunks = [json.loads(c[len("data: ") :].strip()) for c in chunks[1:-2]]
    contents = "".join(c["choices"][0]["delta"].get("content") or "" for c in body_chunks)
    assert "[REFORMULATED]" in contents
    # Avant-dernier : finish_reason=stop. Dernier : [DONE].
    finish = json.loads(chunks[-2][len("data: ") :].strip())
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_emits_narration_live_during_tool_use() -> None:
    """La narration tool_use doit sortir AVANT la reformulation (live
    pendant Pass 1) — pas bufferée.

    Utilise du Markdown pour bypass le skip B2.
    """
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "tool_use", "name": "sirenisateur", "id": "t1", "input": {}},
        {"type": "text", "content": "**Réponse main** avec markdown."},
    )
    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    # Reconstruire la séquence content order
    contents = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            payload = json.loads(c[len("data: ") :].strip())
            d = payload["choices"][0]["delta"]
            if d.get("content"):
                contents.append(d["content"])

    full = "".join(contents)
    # La narration arrive live (pendant Pass 1)
    assert "Je cherche le SIREN" in full
    # La réponse main est passée via le reformulateur (mocké)
    assert "[REFORMULATED]" in full
    # Ordre : narration AVANT reformulé
    narration_idx = full.find("Je cherche le SIREN")
    reformul_idx = full.find("[REFORMULATED]")
    assert narration_idx < reformul_idx


def test_stream_skips_reformulator_on_short_voice_friendly_text() -> None:
    """B2 : si la réponse main est courte + sans Markdown, on skip
    le reformulateur (économie 1.5-2s latence)."""
    body = {"messages": [{"role": "user", "content": "Salut"}], "stream": True}
    fake_events = _events(
        {"type": "text", "content": "Salut, ça va bien merci."},  # court + clean
    )
    refmt_called = []

    def _track(*a: Any, **kw: Any) -> AsyncIterator[str]:
        refmt_called.append(True)
        return _strs("never")

    with (
        patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events),
        patch.object(openai_adapter, "reformulate_for_voice_stream", side_effect=_track),
    ):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    assert refmt_called == [], "skip B2 attendu sur conversationnel court"
    full = "".join(
        json.loads(c[len("data: ") :].strip())["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    )
    # On a quand même le main text yieldé (skip ne yield rien)
    assert "Salut, ça va bien merci." in full


def test_stream_unknown_tool_uses_default_narration() -> None:
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "tool_use", "name": "totally-unknown-tool", "id": "t1", "input": {}},
        {"type": "text", "content": "ok"},
    )
    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    full = "".join(
        json.loads(c[len("data: ") :].strip())["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    )
    assert "Je consulte Pappers" in full


def test_stream_strips_markdown_safety_net() -> None:
    """D : le strip Markdown doit nettoyer ** ## - * du reformulateur."""
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    # Main contient du Markdown pour bypass le skip B2 (force reformulator).
    fake_events = _events({"type": "text", "content": "Main response **with markdown**."})

    # Mock reformulateur qui yield du Markdown impur (cas où Haiku
    # laisse passer malgré la consigne).
    def _polluted_reformulator(*_a: Any, **_kw: Any) -> AsyncIterator[str]:
        return _strs(
            "## Titre\n",
            "Voici **du gras** et _italique_ avec ⚠️ emoji. ",
            "- bullet 1\n- bullet 2.",
        )

    with (
        patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events),
        patch.object(
            openai_adapter, "reformulate_for_voice_stream", side_effect=_polluted_reformulator
        ),
    ):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    full = "".join(
        json.loads(c[len("data: ") :].strip())["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    )
    # Markdown stripped
    assert "**" not in full
    assert "##" not in full
    assert "⚠️" not in full
    # Bullet markers stripped
    assert "- bullet 1" not in full
    assert "bullet 1" in full
    # Le contenu textuel reste
    assert "du gras" in full
    assert "italique" in full


def test_stream_input_rejected_short_circuits_reformulator() -> None:
    """input_rejected = court-circuit, pas de reformulateur."""
    body = {"messages": [{"role": "user", "content": "X"}], "stream": True}
    fake_events = _events(
        {"type": "input_rejected", "reason_code": "input_injection", "reason": ""},
    )
    refmt_called = []

    def _track_reformulator(*a: Any, **kw: Any) -> AsyncIterator[str]:
        refmt_called.append(True)
        return _strs("never")

    with (
        patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events),
        patch.object(
            openai_adapter, "reformulate_for_voice_stream", side_effect=_track_reformulator
        ),
    ):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    assert refmt_called == []
    full = "".join(
        json.loads(c[len("data: ") :].strip())["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    )
    assert "Je ne peux pas traiter cette demande" in full


def test_stream_reformulator_failure_falls_back_to_main_text() -> None:
    """Si le reformulateur lève, on yield le main text strippé Markdown."""
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "text", "content": "Main response **avec markdown**."},
    )

    def _failing_reformulator(*a: Any, **kw: Any):
        async def _gen() -> AsyncIterator[str]:
            raise RuntimeError("anthropic api down")
            yield  # unreachable, but makes this an async generator

        return _gen()

    with (
        patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events),
        patch.object(
            openai_adapter,
            "reformulate_for_voice_stream",
            side_effect=_failing_reformulator,
        ),
    ):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    full = "".join(
        json.loads(c[len("data: ") :].strip())["choices"][0]["delta"].get("content") or ""
        for c in chunks
        if c.startswith("data: ") and not c.startswith("data: [DONE]")
    )
    # Fallback : main text, Markdown strippé
    assert "Main response avec markdown." in full
    assert "**" not in full
    # [DONE] présent
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_done_yielded_before_aclose() -> None:
    """C : [DONE] doit arriver AVANT le aclose() du turn_gen.

    On trace l'ordre via un generator qui logge dans aclose vs le
    moment où chunks contient [DONE]."""
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    order: list[str] = []

    class _TracedGen:
        def __init__(self, evs: list[dict[str, Any]]) -> None:
            self._iter = iter(evs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._iter)
            except StopIteration:
                raise StopAsyncIteration from None

        async def aclose(self):
            order.append("aclose")

    traced = _TracedGen([{"type": "text", "content": "ok"}])

    with patch.object(openai_adapter, "run_guarded_turn", return_value=traced):

        async def _drain_with_tracking() -> None:
            async for c in _stream_chat_completion(body, _FakeRequest()):
                if c == "data: [DONE]\n\n":
                    order.append("done_yielded")

        import asyncio

        asyncio.run(_drain_with_tracking())

    assert "done_yielded" in order
    assert "aclose" in order
    assert order.index("done_yielded") < order.index("aclose"), (
        f"[DONE] doit être yieldé AVANT aclose() — order={order}"
    )


def test_stream_increments_voice_counters() -> None:
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "tool_use", "name": "sirenisateur", "id": "t1", "input": {}},
        {"type": "text", "content": "Hello"},
    )
    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    snap = stats_module.snapshot()
    assert snap["voice_sessions_total"] == 1
    assert snap["voice_custom_llm_calls"] == 1
    assert snap["voice_narration_chunks_emitted"] == 1
    # voice_chars_tts couvre narration ("Je cherche le SIREN… ") +
    # reformulé (mock "[REFORMULATED] Hello").
    assert snap["voice_chars_tts"] > 5


def test_stream_disconnected_increments_cancelled() -> None:
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "text", "content": "Part 1"},
        {"type": "text", "content": "Part 2"},
        {"type": "text", "content": "Part 3"},
    )
    fake_req = _FakeRequest(disconnect_after=1)

    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        _drain_chunks(_stream_chat_completion(body, fake_req))

    snap = stats_module.snapshot()
    assert snap["voice_cancelled_total"] == 1


def test_stream_propagates_voice_system_prompt() -> None:
    """Vérifie que ``compose_voice_system_prompt()`` est passé à
    ``run_guarded_turn`` via ``system_prompt_override``."""
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    captured: dict[str, Any] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _events({"type": "text", "content": "ok"})

    with patch.object(openai_adapter, "run_guarded_turn", side_effect=_fake_run):
        _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    assert "system_prompt_override" in captured["kwargs"]
    spo = captured["kwargs"]["system_prompt_override"]
    assert isinstance(spo, str)
    # Le suffix v2 utilise "MODE VOCAL ACTIF" en majuscules pour
    # signaler l'OVERRIDE explicite de la section "Format de sortie".
    assert "mode vocal actif" in spo.lower()


def test_stream_session_id_uses_user_field_when_provided() -> None:
    body = {
        "messages": [{"role": "user", "content": "Q"}],
        "stream": True,
        "user": "eleven-session-abc",
    }
    captured: dict[str, Any] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        captured["session_id"] = args[2]  # state, user_message, session_id
        return _events({"type": "text", "content": "ok"})

    with patch.object(openai_adapter, "run_guarded_turn", side_effect=_fake_run):
        _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    assert captured["session_id"] == "eleven-session-abc"


def test_stream_session_id_fallback_uuid() -> None:
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    captured: dict[str, Any] = {}

    def _fake_run(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        captured["session_id"] = args[2]
        return _events({"type": "text", "content": "ok"})

    with patch.object(openai_adapter, "run_guarded_turn", side_effect=_fake_run):
        _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    assert captured["session_id"].startswith("voice-")
    assert len(captured["session_id"]) > len("voice-")
