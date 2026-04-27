"""Tests S10 — narrate : mapping correct, défaut, no entity hardcoded."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from genial_agent.voice import narrate as narrate_module
from genial_agent.voice.narrate import _DEFAULT, _TOOL_NARRATION, narrate


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("sirenisateur", "Je cherche le SIREN…"),
        ("recherche-entreprises", "Je regarde les chiffres clés…"),
        ("comptes-entreprise", "Je consulte les comptes…"),
        ("recherche-dirigeants", "Je vérifie les mandats…"),
        ("cartographie-entreprise", "Je trace la cartographie…"),
        ("payload_inspect", "Je détaille les données…"),
    ],
)
def test_narrate_known_tool_returns_expected_phrase(tool_name: str, expected: str) -> None:
    assert narrate(tool_name) == expected


def test_narrate_unknown_tool_returns_default() -> None:
    assert narrate("payload_search") == _DEFAULT
    assert narrate("totally-unknown-tool") == _DEFAULT
    assert narrate("") == _DEFAULT


def test_mapping_size_under_or_equal_six() -> None:
    """Story §"Décisions phase 1" point 2 : ≤ 6 entrées."""
    assert len(_TOOL_NARRATION) <= 6


def test_no_hardcoded_entity_in_module_source() -> None:
    """Aucune entité métier dure dans le module — agent adaptable.

    Cohérent avec la philosophie S09.7 (pas de hardcoding LVMH /
    Carrefour / BNP / Casino dans le code applicatif).
    """
    src = inspect.getsource(narrate_module)
    for entity in ("LVMH", "Carrefour", "BNP", "Casino"):
        assert entity not in src, (
            f"{entity!r} hardcoded in voice/narrate.py — agent must remain "
            f"adaptable (cf. philosophie S09.7)."
        )


def test_module_file_no_hardcoded_entity() -> None:
    """Vérification redondante au niveau fichier (au cas où une chaîne
    serait insérée dans un commentaire mais pas vue par ``inspect``)."""
    path = Path(narrate_module.__file__)
    text = path.read_text(encoding="utf-8")
    for entity in ("LVMH", "Carrefour", "BNP", "Casino"):
        assert entity not in text, f"{entity!r} hardcoded in narrate.py file"
