"""Tests unitaires post-process UI (S06)."""

from __future__ import annotations

from genial_agent.ui.post_process import linkify_sirens, model_badge


def test_siren_linkified() -> None:
    text = "LVMH SIREN 775670417 est une société française."
    out = linkify_sirens(text)
    assert "[775670417](https://www.pappers.fr/entreprise/775670417)" in out


def test_multiple_sirens_all_linkified() -> None:
    text = "Compare 775670417 et 388912497."
    out = linkify_sirens(text)
    assert out.count("pappers.fr/entreprise") == 2
    assert "[775670417](" in out
    assert "[388912497](" in out


def test_no_siren_unchanged() -> None:
    text = "Pas de SIREN ici."
    assert linkify_sirens(text) == text


def test_siren_inside_longer_number_not_matched() -> None:
    """``\\b`` Python ne split pas entre 2 digits — un SIREN à l'intérieur
    d'un nombre 12-chiffres ne doit pas matcher."""
    text = "Valeur : 123456789012"
    out = linkify_sirens(text)
    assert "pappers.fr" not in out


def test_short_number_not_matched() -> None:
    """Un nombre < 9 chiffres n'est pas un SIREN."""
    text = "Code postal 75001 ou 12345678."
    out = linkify_sirens(text)
    assert "pappers.fr" not in out


def test_siren_at_string_boundaries() -> None:
    """Frontière de mot fonctionne en début et fin de string."""
    out = linkify_sirens("775670417 est le SIREN de LVMH 388912497")
    assert out.count("pappers.fr/entreprise") == 2


def test_siren_already_linked_not_double_encoded_visibly() -> None:
    """Un SIREN déjà lié subit la regex (limitation connue : double sub)
    mais le résultat reste lisible — accepté pour MVP. Le code appelant
    est tenu d'appeler ``linkify_sirens`` une seule fois par réponse
    (cf. logique S06 ``app.on_message``)."""
    text = "Voir [775670417](https://www.pappers.fr/entreprise/775670417)."
    out = linkify_sirens(text)
    assert "775670417" in out
    # Markdown tolère le double-encodage mais l'URL reste valide.
    assert "https://www.pappers.fr/entreprise/775670417" in out


def test_model_badge_haiku() -> None:
    assert model_badge(model_used="haiku", escalated=False, escalation_mode=None) == "⚡ Haiku"


def test_model_badge_sonnet_keyword() -> None:
    assert model_badge(model_used="sonnet", escalated=False, escalation_mode=None) == "🧠 Sonnet"


def test_model_badge_escalated_self() -> None:
    out = model_badge(model_used="sonnet", escalated=True, escalation_mode="self")
    assert "Sonnet" in out
    assert "auto-déclenché" in out
    assert "⚡→🧠" in out


def test_model_badge_escalated_forced() -> None:
    out = model_badge(model_used="sonnet", escalated=True, escalation_mode="forced")
    assert "Sonnet" in out
    assert "cap" in out
    assert "⚡→🧠" in out


def test_model_badge_escalated_unknown_mode() -> None:
    """Mode inconnu (None ou autre) ne crashe pas — fallback ``cap``
    branch (cohérent : si escalated=True sans mode==self, c'est un cap)."""
    out = model_badge(model_used="sonnet", escalated=True, escalation_mode=None)
    assert "Sonnet" in out
    assert "⚡→🧠" in out
