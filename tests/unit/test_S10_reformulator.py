"""Tests S10 — voice/reformulator : strip_markdown + system prompt + stream."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from genial_agent.voice import reformulator
from genial_agent.voice.reformulator import (
    REFORMULATOR_SYSTEM_PROMPT,
    reformulate_for_voice_stream,
    strip_markdown_for_tts,
)

# --------------------------------------------------------------------------- #
# strip_markdown_for_tts
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected_substr_in", "expected_substr_not_in"),
    [
        ("Voici **du gras** ici", ["du gras"], ["**"]),
        ("_italique_ test", ["italique"], ["_italique_"]),
        ("## Titre H2\nSuite", ["Titre H2", "Suite"], ["##"]),
        ("- bullet 1\n- bullet 2", ["bullet 1", "bullet 2"], ["- bullet"]),
        ("* bullet star\n* autre", ["bullet star"], ["* "]),
        ("Avec emoji ⚠️ ici", ["Avec emoji", "ici"], ["⚠️"]),
        # Mix : `##` en début de ligne (Markdown valide) → stripped.
        (
            "## titre\n**gras** _italique_ ⚠️",
            ["titre", "gras", "italique"],
            ["**", "_italique_", "⚠️"],
        ),
        # Pas d'altération sur du texte propre
        ("Texte parfaitement propre.", ["Texte parfaitement propre."], []),
    ],
)
def test_strip_markdown(
    text: str, expected_substr_in: list[str], expected_substr_not_in: list[str]
) -> None:
    out = strip_markdown_for_tts(text)
    for s in expected_substr_in:
        assert s in out, f"expected {s!r} in stripped output {out!r}"
    for s in expected_substr_not_in:
        assert s not in out, f"unexpected {s!r} in stripped output {out!r}"


def test_strip_preserves_punctuation_for_tts_boundaries() -> None:
    """Le strip ne doit PAS toucher aux espaces / virgules / points qui
    sont les boundaries voice-friendly du TTS Eleven."""
    text = "Bonjour, voici. Comment vas-tu ? Très bien !"
    out = strip_markdown_for_tts(text)
    assert out == text  # identique


def test_strip_orphan_asterisks_removed() -> None:
    """Les asterisks orphelins (entre espaces) sont retirés pour éviter
    que TTS lit "astérisque"."""
    text = "Texte avec * orphelin * au milieu"
    out = strip_markdown_for_tts(text)
    assert "*" not in out


# --------------------------------------------------------------------------- #
# REFORMULATOR_SYSTEM_PROMPT
# --------------------------------------------------------------------------- #


def test_system_prompt_contains_strict_rules() -> None:
    p = REFORMULATOR_SYSTEM_PROMPT
    # Règles clés
    for kw in (
        "AUCUN MARKDOWN",
        "AUCUN SIREN",
        "AUCUNE DATE ISO",
        "AUCUNE ADRESSE",
        "AUCUNE DÉCIMALE",
        "AUCUN EMOJI",
        "selon Pappers",
    ):
        assert kw in p, f"system prompt missing rule keyword: {kw!r}"


def test_system_prompt_no_hardcoded_entity() -> None:
    """Cohérent avec philosophie S09.7 : pas d'entité métier hardcodée."""
    src = Path(reformulator.__file__).read_text(encoding="utf-8")
    for entity in ("LVMH", "Carrefour", "BNP", "Casino"):
        assert entity not in src, f"{entity!r} hardcoded in reformulator.py"


def test_module_source_is_self_contained() -> None:
    """Sanity : le module compile et expose les symboles publics attendus."""
    assert callable(reformulate_for_voice_stream)
    assert callable(strip_markdown_for_tts)
    assert isinstance(REFORMULATOR_SYSTEM_PROMPT, str)
    assert len(REFORMULATOR_SYSTEM_PROMPT) > 500


# --------------------------------------------------------------------------- #
# reformulate_for_voice_stream — empty input shortcut
# --------------------------------------------------------------------------- #


def _drain(stream: AsyncIterator[str]) -> list[str]:
    import asyncio

    async def _run() -> list[str]:
        return [c async for c in stream]

    return asyncio.run(_run())


def test_reformulator_skips_anthropic_call_on_empty_text() -> None:
    """Pas d'appel Anthropic inutile si le texte main est vide."""
    # Vérification : on patche AsyncAnthropic au module level, on
    # appelle le reformulator avec texte vide, et on s'assure que le
    # mock n'a JAMAIS été instantié.
    with patch.object(reformulator, "AsyncAnthropic") as mock_anthropic:
        chunks = _drain(reformulate_for_voice_stream("question", ""))
    assert chunks == []
    mock_anthropic.assert_not_called()


def test_reformulator_skips_anthropic_call_on_whitespace_only_text() -> None:
    with patch.object(reformulator, "AsyncAnthropic") as mock_anthropic:
        chunks = _drain(reformulate_for_voice_stream("question", "   \n  \t  "))
    assert chunks == []
    mock_anthropic.assert_not_called()


# --------------------------------------------------------------------------- #
# Integration with adapter — mocked Anthropic stream
# --------------------------------------------------------------------------- #


class _FakeAsyncStream:
    """Mock du context manager retourné par client.messages.stream(...)."""

    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas

    async def __aenter__(self) -> _FakeAsyncStream:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    @property
    def text_stream(self) -> AsyncIterator[str]:
        async def _gen() -> AsyncIterator[str]:
            for d in self._deltas:
                yield d

        return _gen()


class _FakeAnthropicClient:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.messages = self  # alias pour client.messages.stream

    async def __aenter__(self) -> _FakeAnthropicClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def stream(self, **_kw: Any) -> _FakeAsyncStream:
        return _FakeAsyncStream(self._deltas)


def test_reformulator_yields_anthropic_deltas() -> None:
    """Avec un client mocké, on vérifie que les deltas remontent."""
    deltas = ["Bonjour, ", "voici la version voice ", "naturelle."]

    def _factory(*_a: Any, **_kw: Any) -> _FakeAnthropicClient:
        return _FakeAnthropicClient(deltas)

    with patch.object(reformulator, "AsyncAnthropic", side_effect=_factory):
        out = _drain(reformulate_for_voice_stream("question test", "réponse main"))

    assert out == deltas
