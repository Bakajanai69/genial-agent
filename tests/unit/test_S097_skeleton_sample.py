"""S09.7 Axe 2 B1 + B3 — Skeleton enrichi.

Vérifie :

- B1 : annotation ``↹`` (U+21B9) sur les clés numériques pures du dict
  pour signaler "string-key, pas index" au LLM.
- B3 : sample d'item type pour les arrays de dicts au lieu du simple
  ``<dict>[N items]``. Le LLM voit les vrais champs disponibles sans
  avoir à inspect par index.
- Le budget ``INDEX_BUDGET_CHARS = 4_000`` est respecté (test élargi
  pour le payload Carrefour 700K).
"""

from __future__ import annotations

import json

from genial_agent.payload_vault import INDEX_BUDGET_CHARS, build_index


def test_skeleton_annotates_digit_keys_with_marker() -> None:
    """Une clé string numérique pure (ex: ``"2023"``) est annotée
    ``"2023↹"`` dans le skeleton — distingue visuellement string-key
    vs index pour le LLM."""
    raw = json.dumps({"2023": {"resultat_net": 12345}, "2024": {"resultat_net": 67890}})
    index = build_index(raw, "p_x")
    skel = index["_skeleton"]
    keys = list(skel.keys())
    # Au moins une des clés est annotée
    assert any("↹" in k for k in keys), f"aucune clé annotée dans {keys}"
    # Et la valeur sous la clé annotée existe (la clé n'a pas été corrompue)
    annotated_key = next(k for k in keys if "↹" in k)
    assert annotated_key.startswith("2023") or annotated_key.startswith("2024")


def test_skeleton_does_not_annotate_alpha_keys() -> None:
    """Une clé alphabétique normale n'est PAS annotée."""
    raw = json.dumps({"siren": "775670417", "ville": "PARIS"})
    index = build_index(raw, "p_x")
    skel = index["_skeleton"]
    for k in skel:
        assert "↹" not in k


def test_skeleton_array_of_dicts_includes_sample() -> None:
    """B3 : un array de dicts est rendu avec un vrai sample (clés
    visibles) au lieu de ``<dict>[N items]``."""
    raw = json.dumps(
        {
            "resultats": [
                {"siren": "775670417", "nom_entreprise": "LVMH", "role": "PRESIDENT"}
                for _ in range(39)
            ]
        }
    )
    index = build_index(raw, "p_x")
    items_skel = index["_skeleton"]["resultats"]
    assert isinstance(items_skel, list)
    # Le 1er élément doit être un dict-sample (pas une string ``<dict>[N items]``)
    sample = items_skel[0]
    assert isinstance(sample, dict)
    # Les clés du sample correspondent aux clés réelles de l'item
    assert "siren" in sample
    assert "nom_entreprise" in sample
    assert "role" in sample


def test_skeleton_array_of_dicts_includes_count_marker() -> None:
    """L'info N (nombre d'items) reste accessible — exposée en queue
    via ``"…N more dict items"`` ou similaire."""
    raw = json.dumps({"items": [{"id": i} for i in range(42)]})
    index = build_index(raw, "p_x")
    items_skel = index["_skeleton"]["items"]
    assert isinstance(items_skel, list)
    # Soit une chaîne marker dans la liste, soit en queue.
    marker_found = any(isinstance(x, str) and ("more" in x or "items" in x) for x in items_skel)
    assert marker_found, f"pas de marker count visible : {items_skel}"


def test_skeleton_array_with_one_item_no_more_marker() -> None:
    """Pour un array à 1 item, pas besoin de ``…0 more items`` superflu."""
    raw = json.dumps({"items": [{"x": 1}]})
    index = build_index(raw, "p_x")
    items_skel = index["_skeleton"]["items"]
    assert isinstance(items_skel, list)
    assert len(items_skel) == 1
    assert isinstance(items_skel[0], dict)


def test_skeleton_empty_array_returns_empty_list() -> None:
    raw = json.dumps({"items": []})
    index = build_index(raw, "p_x")
    assert index["_skeleton"]["items"] == []


def test_skeleton_array_of_scalars_summarized() -> None:
    """Un array de scalaires (str/int) reste résumé succinctement —
    pas de besoin de sample structurel."""
    raw = json.dumps({"tags": ["foo", "bar", "baz", "qux", "quux"]})
    index = build_index(raw, "p_x")
    tags_skel = index["_skeleton"]["tags"]
    assert isinstance(tags_skel, list)
    # 1er élément = summary du type (ex: ``<str:3>``)
    assert isinstance(tags_skel[0], str)
    assert tags_skel[0].startswith("<")


def test_index_under_budget_with_enriched_skeleton() -> None:
    """Avec le skeleton enrichi, l'index reste sous ``INDEX_BUDGET_CHARS = 4_000``.

    Reprend la fixture ``_big_carrefour_like_payload`` du module S09.5
    pour reproduire un worst-case (~700 K chars en input)."""
    comptes = []
    for year in range(2016, 2025):
        comptes.append(
            {
                "annee": year,
                "date_cloture": f"{year}-12-31",
                "chiffre_affaires": 100_000_000 * (year - 2010),
                "resultat_net": 5_000_000 * (year - 2014),
                "details": "x" * 5_000,
            }
        )
    payload = {
        "siren": "451321335",
        "denomination": "CARREFOUR HYPERMARCHÉS",
        "siege": {"code_postal": "91300", "ville": "MASSY"},
        "comptes": comptes * 15,
        "dirigeants": [{"nom": f"Dirigeant {i}", "siren": "111111111"} for i in range(50)],
    }
    raw = json.dumps(payload, ensure_ascii=False)
    index = build_index(raw, "p_test_big")
    encoded = json.dumps(index, ensure_ascii=False)
    assert len(encoded) <= INDEX_BUDGET_CHARS, (
        f"index = {len(encoded)} chars > budget {INDEX_BUDGET_CHARS}"
    )


def test_inspect_hint_mentions_wildcard_and_introspection() -> None:
    """Le hint d'introspection guide explicitement vers le pattern
    « regarder le sample du skeleton, puis wildcard `[*]`, sinon search »."""
    raw = json.dumps({"items": [{"x": 1}]})
    index = build_index(raw, "p_x")
    hint = index["_inspect_hint"]
    assert isinstance(hint, str)
    # Mention explicite du wildcard
    assert "[*]" in hint or "wildcard" in hint
    # Mention explicite du recursive descent
    assert ".." in hint
