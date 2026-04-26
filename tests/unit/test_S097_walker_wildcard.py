"""S09.7 Axe 1 A2 — Walker wildcard via jsonpath-ng.

Tests purement unitaires (aucun appel réseau). Vérifient que :

- Les paths contenant ``[*]`` / ``..`` / ``[?`` sont délégués à
  jsonpath-ng et retournent l'array de matches (ou la valeur unique).
- Les paths simples (sans wildcard) continuent de passer par
  ``_walk_simple`` (M1 livré S09.5) avec désambiguïsation dict-vs-list
  par contexte. Pas de régression.
- Les erreurs de parse jsonpath sont retournées en ``_error`` plutôt
  que d'exception.
"""

from __future__ import annotations

import json

from genial_agent.payload_vault import inspect

_PAPPERS_LIKE_PAYLOAD = json.dumps(
    {
        "resultats": [
            {"siren": "775670417", "nom_entreprise": "LVMH", "role": "PRESIDENT"},
            {"siren": "552032534", "nom_entreprise": "DIOR", "role": "ADMIN"},
            {"siren": "775670418", "nom_entreprise": "ARNAULT FAMILY", "role": "GERANT"},
        ],
        "comptes": [
            {"annee": 2022, "ca": 100, "resultat_net": 10},
            {"annee": 2023, "ca": 200, "resultat_net": 20},
            {"annee": 2024, "ca": 300, "resultat_net": 30},
        ],
        "siege": {"ville": "PARIS", "cp": "75008"},
    }
)


def test_walker_wildcard_returns_envelope_for_scalar_extraction() -> None:
    """``$.resultats[*].siren`` → enveloppe dict avec les 3 SIRENs +
    sample des autres champs disponibles (S09.7 amélioration 2).

    L'agent voit qu'il existe ``nom_entreprise`` et ``role`` à côté du
    siren, sans avoir à re-faire un inspect — encourage l'extraction
    multi-champs au tour suivant."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$.resultats[*].siren")
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    assert set(parsed["_extracted_values"]) == {"775670417", "552032534", "775670418"}
    assert parsed["_count"] == 3
    # L'agent voit qu'il existe d'autres champs frères
    assert "_other_fields_available" in parsed
    assert "nom_entreprise" in parsed["_other_fields_available"]
    assert "role" in parsed["_other_fields_available"]


def test_walker_wildcard_array_value_extraction() -> None:
    """``$.comptes[*].annee`` → enveloppe avec les 3 années en scalaires."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$.comptes[*].annee")
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    assert sorted(parsed["_extracted_values"]) == [2022, 2023, 2024]


def test_walker_recursive_descent() -> None:
    """``$..siren`` retourne tous les SIRENs trouvés, peu importe la
    profondeur. Format dépend du type de match : enveloppe si scalaires,
    list directe si dicts/list."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$..siren")
    parsed = json.loads(out)
    # Les `siren` du payload sont des scalaires → enveloppe
    values = set(parsed["_extracted_values"]) if isinstance(parsed, dict) else set(parsed)
    assert values >= {"775670417", "552032534", "775670418"}


def test_walker_wildcard_returns_full_items_when_no_terminal_field() -> None:
    """``$.resultats[*]`` (sans ``.field`` terminal) → array brut des
    items dict, pas d'enveloppe (l'agent voit déjà toute la struct)."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$.resultats[*]")
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert len(parsed) == 3
    assert all(isinstance(item, dict) for item in parsed)
    assert {item["siren"] for item in parsed} == {"775670417", "552032534", "775670418"}


def test_walker_simple_path_not_delegated() -> None:
    """Un path sans wildcard reste sur le walker simple (non régression M1).

    Pour le prouver de façon déterministe : ``$.2023[0]`` sur un dict avec
    clé string numérique ne marche QUE via ``_walk_simple`` (la
    désambiguïsation contexte est custom — jsonpath-ng standard
    interpréterait `2023` comme index numérique).
    """
    raw = json.dumps({"2023": [{"resultat_net": 12345}]})
    out = inspect(raw, "$.2023[0].resultat_net")
    assert json.loads(out) == 12345


def test_walker_invalid_jsonpath_returns_error() -> None:
    """Parse error jsonpath-ng → ``_error`` exploitable, pas d'exception."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$.foo[*][[bad")
    parsed = json.loads(out)
    assert "_error" in parsed


def test_walker_wildcard_no_match_returns_error() -> None:
    """Wildcard valide mais 0 match → ``_error`` exploitable, pas un null."""
    out = inspect(_PAPPERS_LIKE_PAYLOAD, "$.does_not_exist[*].field")
    parsed = json.loads(out)
    assert "_error" in parsed


def test_walker_wildcard_single_match_returns_value() -> None:
    """1 match unique → la valeur scalaire, pas une liste à 1 élément.

    Plus ergonomique pour le LLM (pas de confusion list-vs-scalar).
    """
    raw = json.dumps({"items": [{"only": "one"}]})
    out = inspect(raw, "$.items[*].only")
    assert json.loads(out) == "one"


def test_walker_wildcard_truncated_when_too_large() -> None:
    """Wildcard sur un gros array → respect du cap ``max_chars``
    (filet ``inspect()`` existant)."""
    big = json.dumps({"items": [{"v": "x" * 1000} for _ in range(50)]})
    out = inspect(big, "$.items[*].v", max_chars=2000)
    assert "[lookup truncated]" in out
