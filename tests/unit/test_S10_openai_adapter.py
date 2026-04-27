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


def test_stream_emits_role_first_then_text_then_done() -> None:
    body = {"messages": [{"role": "user", "content": "Donne-moi LVMH"}], "stream": True}
    fake_req = _FakeRequest()

    fake_events = _events(
        {"type": "text", "content": "Bonjour, "},
        {"type": "text", "content": "voici la réponse."},
        {"type": "end", "reason": "end_turn"},
    )

    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        chunks = _drain_chunks(_stream_chat_completion(body, fake_req))

    # Premier chunk : delta.role = assistant.
    first = json.loads(chunks[0][len("data: ") :].strip())
    assert first["choices"][0]["delta"] == {"role": "assistant"}
    # Chunks de texte intermédiaires.
    body_chunks = [json.loads(c[len("data: ") :].strip()) for c in chunks[1:-2]]
    assert any(c["choices"][0]["delta"].get("content") == "Bonjour, " for c in body_chunks)
    assert any(c["choices"][0]["delta"].get("content") == "voici la réponse." for c in body_chunks)
    # Avant-dernier : finish_reason=stop. Dernier : [DONE].
    finish = json.loads(chunks[-2][len("data: ") :].strip())
    assert finish["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1] == "data: [DONE]\n\n"


def test_stream_emits_narration_on_tool_use() -> None:
    body = {"messages": [{"role": "user", "content": "Q"}], "stream": True}
    fake_events = _events(
        {"type": "tool_use", "name": "sirenisateur", "id": "t1", "input": {}},
        {"type": "text", "content": "Réponse."},
    )
    with patch.object(openai_adapter, "run_guarded_turn", return_value=fake_events):
        chunks = _drain_chunks(_stream_chat_completion(body, _FakeRequest()))

    contents = []
    for c in chunks:
        if c.startswith("data: ") and not c.startswith("data: [DONE]"):
            payload = json.loads(c[len("data: ") :].strip())
            d = payload["choices"][0]["delta"]
            if d.get("content"):
                contents.append(d["content"])

    full = "".join(contents)
    assert "Je cherche le SIREN" in full
    assert "Réponse." in full


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
    # voice_chars_tts couvre narration ("Je cherche le SIREN… ") + "Hello".
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
    assert "Mode vocal actif" in spo


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
