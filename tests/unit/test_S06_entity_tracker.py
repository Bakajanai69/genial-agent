"""Tests unitaires entity tracker (S06)."""

from __future__ import annotations

import json

from genial_agent.ui.entity_tracker import (
    ActiveEntity,
    TurnTracker,
    extract_active_entity,
    format_banner,
)


def _tracker_with(
    *,
    inputs: list[dict] | None = None,
    previews: list[str] | None = None,
) -> TurnTracker:
    t = TurnTracker()
    for inp in inputs or []:
        t.record_tool_use(inp)
    for pr in previews or []:
        t.record_tool_result(pr)
    return t


def test_extract_from_preview_json() -> None:
    preview = json.dumps({"siren": "775670417", "denomination": "LVMH"})
    entity = extract_active_entity(_tracker_with(previews=[preview]))
    assert entity == ActiveEntity(name="LVMH", siren="775670417")


def test_extract_returns_last_preview_entity() -> None:
    """Sur 2 entités résolues, on garde la dernière (chaînage U3 type
    Carrefour puis Casino → Casino reste actif pour le follow-up)."""
    p1 = json.dumps({"siren": "111111111", "denomination": "AAA"})
    p2 = json.dumps({"siren": "775670417", "denomination": "LVMH"})
    entity = extract_active_entity(_tracker_with(previews=[p1, p2]))
    assert entity is not None
    assert entity.siren == "775670417"
    assert entity.name == "LVMH"


def test_extract_falls_back_to_input_name_plus_preview_siren() -> None:
    inp = {"company_name": "Carrefour", "country_code": "FR"}
    preview = "Carrefour SA — SIREN 652014051, siège..."
    entity = extract_active_entity(_tracker_with(inputs=[inp], previews=[preview]))
    assert entity is not None
    assert entity.name == "Carrefour"
    assert entity.siren == "652014051"


def test_extract_from_input_with_full_couple() -> None:
    """Si l'input lui-même contient name + siren (rare, mais possible
    sur ``recherche-dirigeants`` où l'agent a déjà résolu le SIREN),
    on prend tel quel sans aller plus loin."""
    inp = {"company_name": "LVMH", "siren": "775670417"}
    entity = extract_active_entity(_tracker_with(inputs=[inp]))
    assert entity == ActiveEntity(name="LVMH", siren="775670417")


def test_extract_none_when_no_data() -> None:
    assert extract_active_entity(TurnTracker()) is None


def test_extract_none_when_only_random_numbers() -> None:
    """Du texte avec un nombre 9-chiffres mais aucun nom = None."""
    preview = "Valeur 552032534 brute, aucune entité."
    entity = extract_active_entity(_tracker_with(previews=[preview]))
    assert entity is None


def test_extract_ignores_invalid_json() -> None:
    """Un preview tronqué non-JSON ne crashe pas, on saute au suivant."""
    p1 = "Carrefour SA — texte non-JSON 652014051"
    p2 = json.dumps({"siren": "775670417", "denomination": "LVMH"})
    entity = extract_active_entity(_tracker_with(previews=[p1, p2]))
    assert entity is not None
    assert entity.siren == "775670417"


def test_extract_handles_siren_with_spaces() -> None:
    """Pappers renvoie parfois des SIREN avec espaces (775 670 417)."""
    preview = json.dumps({"siren": "775 670 417", "denomination": "LVMH"})
    entity = extract_active_entity(_tracker_with(previews=[preview]))
    assert entity is not None
    assert entity.siren == "775670417"


def test_extract_uses_alternate_name_keys() -> None:
    """``denomination_usuelle`` et ``nom_entreprise`` sont des
    fallbacks valides côté Pappers."""
    preview = json.dumps({"siren": "775670417", "nom_entreprise": "ACME SAS"})
    entity = extract_active_entity(_tracker_with(previews=[preview]))
    assert entity is not None
    assert entity.name == "ACME SAS"


def test_format_banner_shape() -> None:
    b = format_banner(ActiveEntity(name="LVMH", siren="775670417"))
    assert b is not None
    assert "LVMH" in b
    assert "775670417" in b
    assert "pappers.fr/entreprise/775670417" in b
    assert "Entité active" in b


def test_format_banner_none() -> None:
    assert format_banner(None) is None


def test_tracker_default_lists_independent() -> None:
    """Garde-fou contre un piège ``mutable default`` masqué — chaque
    instance de ``TurnTracker`` doit avoir ses propres listes."""
    a, b = TurnTracker(), TurnTracker()
    a.record_tool_use({"x": 1})
    a.record_tool_result("preview a")
    assert b.tool_use_inputs == []
    assert b.tool_result_previews == []


def test_tracker_silently_ignores_non_dict_input() -> None:
    """Forward-compat S07/S10 : si un event futur passe une string en
    ``input``, on ignore plutôt que de crasher."""
    t = TurnTracker()
    t.record_tool_use("not a dict")  # type: ignore[arg-type]
    t.record_tool_result(123)  # type: ignore[arg-type]
    assert t.tool_use_inputs == []
    assert t.tool_result_previews == []
