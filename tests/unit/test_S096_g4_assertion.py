"""Tests S09.6 (review P0-2) — assertion G4 ``_has_resultat_net_2023``.

Avant le fix, la regex secondaire ``\\d{4,}`` faisait passer un texte qui
ne contenait QUE le SIREN (775670417, 9 chiffres) — faux positif majeur.
Le fix exige un montant monétaire à proximité de la métrique.

Ces tests vivent en ``tests/unit`` parce qu'ils testent une fonction
pure (string in, bool out). Ils n'ont besoin ni de l'agent ni des
crédits Pappers.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Le module test_S095_golden_prompts vit dans tests/integration/, pas
# importable par défaut. On charge la fonction directement via importlib.
GOLDEN_PATH = Path(__file__).resolve().parents[1] / "integration" / "test_S095_golden_prompts.py"


def _load_assertion():
    import importlib.util

    spec = importlib.util.spec_from_file_location("_test_S095_golden_prompts_for_unit", GOLDEN_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module._has_resultat_net_2023


_has_resultat_net_2023 = _load_assertion()


def test_passes_with_proper_money_amount() -> None:
    """Cas nominal : montant formaté + métrique + année."""
    text = (
        "Le résultat net de LVMH pour 2023 s'élève à 9 587 500 000 € "
        "(SIREN 775670417, bilan clos 31/12/2023)."
    )
    ok, reason = _has_resultat_net_2023(text)
    assert ok, reason


def test_passes_with_unit_suffix() -> None:
    """Variante : nombre + unité monétaire (M€, milliards…)."""
    text = "Le résultat net 2024 atteint 12 milliards d'euros."
    ok, reason = _has_resultat_net_2023(text)
    assert ok, reason


def test_passes_with_billion_compact() -> None:
    """Variante : '9.5 milliards' à proximité du résultat net."""
    text = "Le résultat net 2023 ressort à 9.5 milliards."
    ok, reason = _has_resultat_net_2023(text)
    assert ok, reason


# ── Régression : faux positifs avant fix P0-2 ──────────────────────────


def test_rejects_only_siren_no_amount() -> None:
    """Faux positif identifié review P0-2 : un texte qui contient
    seulement le SIREN (9 chiffres) ne doit pas valider."""
    text = (
        "Le résultat net de LVMH 2023 n'est pas accessible "
        "(SIREN 775670417). Données API Pappers indisponibles."
    )
    ok, reason = _has_resultat_net_2023(text)
    assert not ok
    assert "montant monétaire" in reason


def test_rejects_only_year_no_amount() -> None:
    """Variante : juste une année (4 chiffres) ne suffit pas."""
    text = "Le résultat net pour l'année 2023 n'est pas disponible."
    ok, reason = _has_resultat_net_2023(text)
    assert not ok


def test_rejects_amount_far_from_metric() -> None:
    """Un montant cité 200 chars plus loin (autre sujet) ne doit pas
    valider la métrique 2023."""
    # Texte long sans point pour rester dans le même "block" mais on
    # excède quand même la fenêtre de 80 chars de la regex de proximité.
    text = (
        "Le résultat net 2023 de LVMH n'est malheureusement pas accessible "
        "via l'API Pappers en ce moment ; cette donnée nécessite l'accès "
        "comptes-entreprise qui est indisponible aujourd'hui ; "
        "à titre indicatif on peut mentionner que le chiffre d'affaires "
        "groupe est de 84 milliards d'euros sur l'exercice"
    )
    ok, reason = _has_resultat_net_2023(text)
    assert not ok, f"Expected reject mais ok=True (reason='{reason}')"


def test_rejects_no_metric_keyword() -> None:
    text = "Le chiffre d'affaires 2023 atteint 9 587 500 000 €."
    ok, reason = _has_resultat_net_2023(text)
    assert not ok
    assert "résultat net" in reason or "bénéfice" in reason


def test_rejects_no_year() -> None:
    text = "Le résultat net 2018 atteint 5 412 000 000 €."
    ok, reason = _has_resultat_net_2023(text)
    assert not ok
    assert "2023" in reason or "2024" in reason


def test_passes_with_benefice_synonym() -> None:
    text = "Le bénéfice 2023 de LVMH est de 12 345 678 euros."
    ok, reason = _has_resultat_net_2023(text)
    assert ok, reason


def test_passes_metric_after_amount() -> None:
    """L'agent peut formuler dans l'ordre inverse : montant puis métrique."""
    text = "9 587 500 000 € (résultat net LVMH 2023)."
    ok, reason = _has_resultat_net_2023(text)
    assert ok, reason
