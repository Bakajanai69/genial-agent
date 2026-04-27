"""Tests S10 — voice/flow : sentence_buffer, fillers, skip reformulator."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from genial_agent.voice.flow import (
    FILLER_INTERVAL_S,
    FILLER_PHRASES,
    next_filler,
    sentence_buffer,
    should_skip_reformulator,
)

# --------------------------------------------------------------------------- #
# A4 — sentence_buffer
# --------------------------------------------------------------------------- #


async def _stream(*deltas: str) -> AsyncIterator[str]:
    for d in deltas:
        yield d


def _drain(stream: AsyncIterator[str]) -> list[str]:
    async def _r() -> list[str]:
        return [c async for c in stream]

    return asyncio.run(_r())


def test_sentence_buffer_flushes_on_period() -> None:
    out = _drain(sentence_buffer(_stream("Bonjour", " ", "monde", ". ", "Suite")))
    # "Bonjour monde. " est flushé d'un coup au boundary, puis "Suite" en fin.
    assert "".join(out) == "Bonjour monde. Suite"
    assert any("Bonjour monde." in c for c in out)


def test_sentence_buffer_groups_fragmented_deltas() -> None:
    """Plusieurs deltas mini regroupés en 1 phrase complète."""
    out = _drain(
        sentence_buffer(_stream("Sa", "lut", " ", "ça", " ", "va", " ", "bien", " ! ", "OK"))
    )
    full = "".join(out)
    assert full == "Salut ça va bien ! OK"
    # Le 1er chunk émis doit être la phrase complète "Salut ça va bien ! "
    assert out[0].rstrip() == "Salut ça va bien !"


def test_sentence_buffer_handles_question_mark() -> None:
    out = _drain(sentence_buffer(_stream("Comment ça va", " ? ", "Très bien")))
    assert "".join(out) == "Comment ça va ? Très bien"
    assert any("Comment ça va ?" in c for c in out)


def test_sentence_buffer_handles_ellipsis() -> None:
    out = _drain(sentence_buffer(_stream("Un instant…", " ", "Voilà")))
    assert "".join(out) == "Un instant… Voilà"


def test_sentence_buffer_force_flush_on_max_chars() -> None:
    """Phrase très longue sans boundary → flush forcé après _MAX_BUFFER_CHARS."""
    long_text = "a" * 300  # > 250 max buffer
    out = _drain(sentence_buffer(_stream(long_text, " end. ")))
    full = "".join(out)
    assert full == long_text + " end. "
    # Le 1er chunk doit être le long_text (flush forcé), pas attendre la fin.
    assert len(out) >= 2


def test_sentence_buffer_flushes_remaining_on_eof() -> None:
    """Pas de boundary final → on flush quand même au end of stream."""
    out = _drain(sentence_buffer(_stream("Pas de point final")))
    assert out == ["Pas de point final"]


def test_sentence_buffer_empty_stream() -> None:
    assert _drain(sentence_buffer(_stream())) == []


def test_sentence_buffer_skips_empty_deltas() -> None:
    out = _drain(sentence_buffer(_stream("", "Hello", "", " world.")))
    assert "".join(out) == "Hello world."


def test_sentence_buffer_does_not_split_on_decimal_comma() -> None:
    """Les virgules de décimale ne doivent PAS déclencher un flush
    (elles ne sont pas dans _SENTENCE_END_RE)."""
    out = _drain(sentence_buffer(_stream("CA de 9,5 milliards", ". Suite")))
    # 1 seul chunk pour la phrase complète "CA de 9,5 milliards. "
    full = "".join(out)
    assert full == "CA de 9,5 milliards. Suite"
    assert "CA de 9,5 milliards." in out[0]


# --------------------------------------------------------------------------- #
# B1 — Fillers
# --------------------------------------------------------------------------- #


def test_filler_phrases_non_empty_and_french() -> None:
    assert len(FILLER_PHRASES) >= 3
    for p in FILLER_PHRASES:
        assert isinstance(p, str)
        assert len(p) > 0
        assert "…" in p or p.endswith(".")


def test_next_filler_rotates_cyclically() -> None:
    n = len(FILLER_PHRASES)
    seen = [next_filler(i) for i in range(n * 2)]
    # Premier cycle = N phrases différentes
    assert len(set(seen[:n])) == n
    # Cycle se répète
    assert seen[:n] == seen[n : n * 2]


def test_next_filler_ends_with_space() -> None:
    """Boundary voice-friendly (espace après) pour TTS streaming."""
    assert next_filler(0).endswith(" ")


def test_filler_interval_reasonable() -> None:
    """Plage acceptable : ni trop court (spam) ni trop long (silence)."""
    assert 2.0 <= FILLER_INTERVAL_S <= 8.0


def test_filler_phrases_no_hardcoded_entity() -> None:
    """Cohérent avec philosophie S09.7 : pas d'entité métier hardcodée."""
    full = " ".join(FILLER_PHRASES)
    for entity in ("LVMH", "Carrefour", "BNP", "Casino", "Pappers"):
        assert entity not in full, f"{entity!r} hardcoded in FILLER_PHRASES"


# --------------------------------------------------------------------------- #
# B2 — should_skip_reformulator
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "expected_skip"),
    [
        # Conversationnel court → skip
        ("Salut !", True),
        ("Oui, ça va bien merci.", True),
        ("Je suis là pour t'aider.", True),
        # Markdown bold → no skip
        ("Voici **les chiffres** principaux.", False),
        # Markdown header → no skip
        ("## Fiche\nSuite", False),
        # Markdown bullet → no skip
        ("- bullet", False),
        # Liste numérotée → no skip
        ("1. Premier\n2. Second", False),
        # Date ISO → no skip
        ("Bilan clos 31/12/2024", False),
        # SIREN 9 chiffres → no skip
        ("SIREN 775670417 actif", False),
        # Long texte → no skip
        ("a" * 200, False),
        # Cas limite : 180 chars exactement = OK (cap inclusif)
        ("a" * 180, True),
        # 181 chars = no skip
        ("a" * 181, False),
    ],
)
def test_should_skip_reformulator(text: str, expected_skip: bool) -> None:
    assert should_skip_reformulator(text) is expected_skip
