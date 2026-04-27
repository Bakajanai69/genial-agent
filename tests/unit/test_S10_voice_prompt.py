"""Tests S10 — voice_prompt : suffixe injecté + prompt principal non muté."""

from __future__ import annotations

from genial_agent.prompts import SYSTEM_PROMPT_AGENT
from genial_agent.voice.voice_prompt import (
    VOICE_SUFFIX,
    compose_voice_system_prompt,
)


def test_voice_suffix_is_concatenated_to_system_prompt() -> None:
    composed = compose_voice_system_prompt()
    assert composed.startswith(SYSTEM_PROMPT_AGENT)
    assert composed.endswith(VOICE_SUFFIX)
    assert "Mode vocal actif" in composed


def test_compose_does_not_mutate_system_prompt() -> None:
    original = SYSTEM_PROMPT_AGENT
    _ = compose_voice_system_prompt()
    # Re-import pour s'assurer que le module n'a pas été monkey-patché.
    from genial_agent import prompts as p2

    assert original == p2.SYSTEM_PROMPT_AGENT
    # Et le SYSTEM_PROMPT_AGENT importé reste identique.
    assert original == SYSTEM_PROMPT_AGENT


def test_voice_suffix_covers_required_rules() -> None:
    """Les 6 règles cibles de la story §"Décisions phase 1" point 1
    doivent être présentes dans le suffixe."""
    rules = [
        "SIREN",  # interdit à l'oral
        "Arrondir",  # arrondir les chiffres
        "narratif",  # style narratif
        "transitions",  # transitions naturelles
        "100-120 mots",  # cap longueur
        "bilan",  # sourçage en interne
    ]
    for keyword in rules:
        assert keyword in VOICE_SUFFIX, f"missing rule keyword: {keyword!r}"


def test_voice_suffix_does_not_hardcode_entities() -> None:
    """Aucune entité métier dure (LVMH, Carrefour, BNP, Casino) — l'agent
    doit rester adaptable (cf. philosophie S09.7)."""
    for entity in ("LVMH", "Carrefour", "BNP", "Casino"):
        assert entity not in VOICE_SUFFIX, f"entity hardcoded: {entity}"
