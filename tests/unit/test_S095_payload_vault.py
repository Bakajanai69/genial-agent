"""Tests unitaires S09.5 — Payload Vault générique.

Pas d'API distante. Tests purement déterministes du module
``genial_agent.payload_vault``. Les tests live (golden prompts G1-G5)
sont dans ``tests/integration/test_S095_golden_prompts.py``.

Couverture (cf. story §"Tests unitaires à produire") :

- Vault : store / get / unicity, isolation cross-instance.
- Index : taille bornée, squelette à profondeur N, array sizes.
- Walk : dotted, relative, $-prefixed, indices négatifs, erreurs.
- Inspect : truncation marker, max_chars hard cap.
- Search : matches + contexte, regex invalide, max_matches cap.
- Edge cases : payload non-JSON, paths vides, types non-string.
- Sécurité : path attribute Python jamais lu.
"""

from __future__ import annotations

import json

from genial_agent.payload_vault import (
    INDEX_BUDGET_CHARS,
    LOOKUP_MAX_CHARS_DEFAULT,
    OFFLOAD_THRESHOLD_CHARS,
    PayloadVault,
    build_index,
    inspect,
    search,
)

# ---------------------------------------------------------------------------
# Vault — store / get
# ---------------------------------------------------------------------------


def test_store_returns_unique_id() -> None:
    """100 stores → 100 IDs distincts (token_hex random)."""
    vault = PayloadVault()
    ids = {vault.store(f"payload {i}") for i in range(100)}
    assert len(ids) == 100


def test_get_returns_verbatim() -> None:
    """Octet-pour-octet : la donnée écrite est égale à la donnée lue."""
    vault = PayloadVault()
    raw = '{"hello": "world", "accents": "société"}'
    pid = vault.store(raw)
    assert vault.get(pid) == raw


def test_get_unknown_id_returns_none() -> None:
    vault = PayloadVault()
    assert vault.get("p_nonexistent") is None
    assert vault.get("") is None


def test_vault_len_tracks_stores() -> None:
    vault = PayloadVault()
    assert len(vault) == 0
    vault.store("a")
    vault.store("b")
    assert len(vault) == 2


def test_vault_clear_drops_all() -> None:
    vault = PayloadVault()
    vault.store("a")
    vault.clear()
    assert len(vault) == 0


def test_vault_instances_are_isolated() -> None:
    """Deux vaults ne partagent pas leur store (default_factory)."""
    a, b = PayloadVault(), PayloadVault()
    a.store("x")
    assert len(b) == 0


def test_payload_id_format() -> None:
    vault = PayloadVault()
    pid = vault.store("anything")
    assert pid.startswith("p_")
    # 4 bytes hex = 8 hex chars + "p_" = 10
    assert len(pid) == 10


# ---------------------------------------------------------------------------
# Index — squelette + array_sizes + previews
# ---------------------------------------------------------------------------


def _big_carrefour_like_payload() -> str:
    """Reproduit la forme générale d'un payload ``comptes-entreprise``
    Pappers : entreprise + array de comptes par année. Taille ~700 K
    pour mimer le worst case Carrefour Hyper (cf. story §"Données
    factuelles")."""
    comptes = []
    for year in range(2016, 2025):
        comptes.append(
            {
                "annee": year,
                "date_cloture": f"{year}-12-31",
                "chiffre_affaires": 100_000_000 * (year - 2010),
                "resultat_net": 5_000_000 * (year - 2014),
                "details": "x" * 5_000,  # padding pour atteindre ~700K
            }
        )
    payload = {
        "siren": "451321335",
        "denomination": "CARREFOUR HYPERMARCHÉS",
        "siege": {"code_postal": "91300", "ville": "MASSY"},
        "comptes": comptes * 15,  # gonfle à ~675 K chars
        "dirigeants": [{"nom": f"Dirigeant {i}", "siren": "111111111"} for i in range(50)],
    }
    return json.dumps(payload, ensure_ascii=False)


def test_build_index_under_4kb() -> None:
    """L'index produit pour un payload 700K doit rester sous le budget
    INDEX_BUDGET_CHARS — sinon le LLM voit une 2e fois le contenu."""
    raw = _big_carrefour_like_payload()
    assert len(raw) > 100_000  # sanity : le fixture est bien gros
    index = build_index(raw, "p_test123")
    encoded = json.dumps(index, ensure_ascii=False)
    assert len(encoded) <= INDEX_BUDGET_CHARS, (
        f"index = {len(encoded)} chars > budget {INDEX_BUDGET_CHARS}"
    )


def test_index_contains_required_fields() -> None:
    raw = '{"foo": [1, 2, 3], "bar": "baz"}'
    index = build_index(raw, "p_x")
    for field_ in (
        "_payload_id",
        "_size_chars",
        "_skeleton",
        "_array_sizes",
        "_preview_head",
        "_preview_tail",
        "_inspect_hint",
    ):
        assert field_ in index, f"missing index field: {field_}"
    assert index["_payload_id"] == "p_x"
    assert index["_size_chars"] == len(raw)


def test_index_preview_head_and_tail() -> None:
    """Preview head ≠ preview tail quand le payload est plus long que
    PREVIEW_HEAD_CHARS."""
    raw = "A" * 1_000 + "Z" * 1_000  # 2K chars distincts
    index = build_index(raw, "p_x")
    assert index["_preview_head"].startswith("A")
    assert index["_preview_tail"].endswith("Z")


def test_index_array_sizes_collected() -> None:
    """Un array de N éléments → ``_array_sizes["$.path"] == N``."""
    raw = json.dumps({"comptes": [{"a": 1} for _ in range(42)]})
    index = build_index(raw, "p_x")
    assert index["_array_sizes"]["$.comptes"] == 42


def test_index_skeleton_max_depth() -> None:
    """Au-delà de SKELETON_MAX_DEPTH, l'arbre est résumé en ``<...>``."""
    deep = {"l1": {"l2": {"l3": {"l4": {"deep_value": True}}}}}
    raw = json.dumps(deep)
    index = build_index(raw, "p_x")
    skeleton = index["_skeleton"]
    # On atteint la profondeur 3 max. Au-delà, le contenu est
    # résumé. Donc skeleton["l1"]["l2"]["l3"] doit être un summary
    # (chaîne ``<...>``), pas l'arbre complet.
    summary = skeleton["l1"]["l2"]["l3"]
    assert isinstance(summary, str)
    assert summary.startswith("<")


def test_index_skeleton_summarizes_arrays() -> None:
    """Les arrays sont résumés en ``<type>[N items]``, pas étalés."""
    raw = json.dumps({"items": [{"id": i} for i in range(100)]})
    index = build_index(raw, "p_x")
    items_skel = index["_skeleton"]["items"]
    assert isinstance(items_skel, list)
    assert items_skel == ["<dict>[100 items]"]


def test_index_handles_non_json_payload() -> None:
    """Payload texte brut (non-JSON) → squelette ``_raw + _note`` (cf.
    ``_safe_parse_json`` fallback)."""
    raw = "this is not json at all, just text"
    index = build_index(raw, "p_x")
    skeleton = index["_skeleton"]
    assert isinstance(skeleton, dict)
    assert "_raw" in skeleton
    assert "_note" in skeleton


def test_index_short_payload_no_tail() -> None:
    """Payload < PREVIEW_HEAD_CHARS → preview_tail vide (pas de
    duplication tête/queue)."""
    raw = "small"
    index = build_index(raw, "p_x")
    assert index["_preview_tail"] == ""


# ---------------------------------------------------------------------------
# Walk — JSONPath simplifié
# ---------------------------------------------------------------------------


_SAMPLE = json.dumps(
    {
        "siren": "775670417",
        "comptes": [
            {"annee": 2022, "ca": 100},
            {"annee": 2023, "ca": 200},
            {"annee": 2024, "ca": 300},
        ],
        "siege": {"ville": "PARIS", "cp": "75008"},
    }
)


def test_walk_dotted_path_with_dollar() -> None:
    """``$.a.b[0].c`` → valeur correcte."""
    out = inspect(_SAMPLE, "$.comptes[0].annee")
    assert json.loads(out) == 2022


def test_walk_relative_path_no_dollar() -> None:
    """``a.b[0].c`` (sans ``$``) → même résultat que ``$.a.b[0].c``."""
    out_dollar = inspect(_SAMPLE, "$.comptes[1].ca")
    out_relative = inspect(_SAMPLE, "comptes[1].ca")
    assert out_dollar == out_relative
    assert json.loads(out_relative) == 200


def test_walk_negative_index_last_element() -> None:
    """``arr[-1]`` retourne le dernier élément (pattern: bilan le plus
    récent dans un array trié chronologiquement croissant)."""
    out = inspect(_SAMPLE, "$.comptes[-1].annee")
    assert json.loads(out) == 2024


def test_walk_invalid_path_returns_error() -> None:
    """Path inexistant → dict ``{"_error": "..."}``, pas d'exception."""
    out = inspect(_SAMPLE, "$.does.not.exist")
    parsed = json.loads(out)
    assert "_error" in parsed


def test_walk_index_out_of_range() -> None:
    out = inspect(_SAMPLE, "$.comptes[100]")
    parsed = json.loads(out)
    assert "_error" in parsed
    assert "out of range" in parsed["_error"]


def test_walk_root_path_returns_full_tree() -> None:
    """``$`` ou ``""`` → le payload entier."""
    out = inspect(_SAMPLE, "$")
    parsed = json.loads(out)
    assert parsed["siren"] == "775670417"


def test_walk_traverses_nested_dict() -> None:
    out = inspect(_SAMPLE, "$.siege.ville")
    assert json.loads(out) == "PARIS"


def test_walk_does_not_expose_python_attrs() -> None:
    """Sécurité (review S09.5) : un path qui ressemble à un attribut
    Python (``__class__``, ``__dict__``) est traité comme une clé de
    dict ordinaire — donc rejeté avec ``_error``, pas exécuté."""
    out = inspect(_SAMPLE, "$.__class__")
    parsed = json.loads(out)
    assert "_error" in parsed
    out2 = inspect(_SAMPLE, "$.__dict__.foo")
    parsed2 = json.loads(out2)
    assert "_error" in parsed2


def test_walk_non_string_path_returns_error() -> None:
    out = inspect(_SAMPLE, None)  # type: ignore[arg-type]
    parsed = json.loads(out)
    assert "_error" in parsed


# --- Walker dict-key disambiguation (fix S09.5 review M1) ------------------


def test_walk_digit_key_in_dict() -> None:
    """Fix M1 : un segment numérique pur sur un ``dict`` est interprété
    comme **clé string**, pas comme index d'array.

    Cas Pappers ``comptes-entreprise(annee=2023)`` qui retourne
    ``{"2023": [...]}`` : avant le fix, ``$.2023[0]`` était rejeté à
    tort (``"not a list at '2023'"``) — c'est exactement ce qui a fait
    rater l'extraction de la valeur exacte du résultat net 2023 LVMH
    en G4. Cf. ``traces/S095_iterations.md`` §G4.
    """
    raw = json.dumps(
        {
            "2023": [{"resultat_net": 12345}, {"resultat_net": 67890}],
            "comptes": [{"label": "fallback"}],
        }
    )
    out = inspect(raw, "$.2023[0].resultat_net")
    assert json.loads(out) == 12345

    # Variantes de notation
    out2 = inspect(raw, "2023[0].resultat_net")
    assert json.loads(out2) == 12345


def test_walk_digit_key_in_dict_with_negative_index() -> None:
    """Combinaison : clé string numérique + index négatif derrière."""
    raw = json.dumps({"2023": [{"v": "a"}, {"v": "b"}, {"v": "c"}]})
    out = inspect(raw, "$.2023[-1].v")
    assert json.loads(out) == "c"


def test_walk_index_on_list_still_works() -> None:
    """Non-régression : indices numériques sur arrays continuent de
    fonctionner après le fix dict-key."""
    raw = json.dumps({"items": [{"v": "a"}, {"v": "b"}, {"v": "c"}]})
    assert json.loads(inspect(raw, "$.items[0].v")) == "a"
    assert json.loads(inspect(raw, "$.items[-1].v")) == "c"
    # Index sans crochets non plus (segment numérique sur list)
    assert json.loads(inspect(raw, "items.1.v")) == "b"


def test_walk_alpha_key_on_list_returns_error() -> None:
    """Erreur explicite : un segment non-numérique sur une list est
    rejeté avec un message qui indique le nombre d'items disponibles."""
    raw = json.dumps({"items": ["a", "b"]})
    out = inspect(raw, "$.items.foo")
    parsed = json.loads(out)
    assert "_error" in parsed
    assert "list expects integer index" in parsed["_error"]
    assert "2 items" in parsed["_error"]


def test_walk_navigates_into_scalar_returns_error() -> None:
    """Naviguer dans un scalaire (str/int) renvoie une erreur claire."""
    raw = json.dumps({"name": "Alice"})
    out = inspect(raw, "$.name.foo")
    parsed = json.loads(out)
    assert "_error" in parsed
    assert "cannot navigate into str" in parsed["_error"]


def test_walk_dict_priority_over_list_interpretation() -> None:
    """Quand le payload a une clé string ``"0"`` à un même niveau qu'un
    array sibling, la clé string gagne (le contexte est dict)."""
    raw = json.dumps({"0": {"label": "string-key zero"}, "items": ["a", "b"]})
    out = inspect(raw, "$.0.label")
    assert json.loads(out) == "string-key zero"


# ---------------------------------------------------------------------------
# Inspect — truncation
# ---------------------------------------------------------------------------


def test_inspect_truncation_marker_when_too_long() -> None:
    """Valeur > max_chars → marker ``[lookup truncated]`` en queue."""
    big = json.dumps({"data": "x" * 50_000})
    out = inspect(big, "$.data", max_chars=200)
    assert "[lookup truncated]" in out
    assert len(out) <= 200 + len("\n…[lookup truncated]")


def test_inspect_max_chars_hard_cap() -> None:
    """Si l'agent envoie max_chars > LOOKUP_MAX_CHARS_HARD, on cap."""
    big = json.dumps({"data": "x" * 50_000})
    out = inspect(big, "$.data", max_chars=999_999)
    # Le hard cap est 12_000 — donc même demande 999K, on tronque à 12K.
    assert len(out) <= 12_000 + len("\n…[lookup truncated]")


def test_inspect_default_max_chars() -> None:
    """``max_chars`` par défaut est ``LOOKUP_MAX_CHARS_DEFAULT``."""
    big = json.dumps({"data": "x" * 50_000})
    out_default = inspect(big, "$.data")
    out_explicit = inspect(big, "$.data", max_chars=LOOKUP_MAX_CHARS_DEFAULT)
    assert out_default == out_explicit


# ---------------------------------------------------------------------------
# Search — regex grep
# ---------------------------------------------------------------------------


def test_search_returns_matches_with_context() -> None:
    raw = "Le SIREN 75008 et la suite\net une autre 75009 plus loin"
    matches = search(raw, r"\d{5}", max_matches=10, context_chars=30)
    assert len(matches) == 2
    for m in matches:
        assert "match" in m and "context" in m and "position" in m
        assert m["match"].isdigit()


def test_search_invalid_regex_returns_error() -> None:
    matches = search("anything", r"(unclosed")
    assert matches and "error" in matches[0]


def test_search_max_matches_cap() -> None:
    raw = "abc " * 50  # 50 occurrences de "abc"
    matches = search(raw, "abc", max_matches=10)
    assert len(matches) == 10


def test_search_hard_max_matches_cap() -> None:
    """Si l'agent envoie max_matches > 30, on cap dur (security)."""
    raw = "x " * 100
    matches = search(raw, "x", max_matches=999)
    assert len(matches) <= 30


def test_search_case_insensitive() -> None:
    """``re.IGNORECASE`` est activé par défaut."""
    raw = "Carrefour and CARREFOUR"
    matches = search(raw, "carrefour")
    assert len(matches) == 2


def test_search_pattern_truncated_if_too_long() -> None:
    """Pattern > MAX_REGEX_PATTERN_CHARS → tronqué (pas une erreur)."""
    long_pattern = "x" * 1_000
    # On vérifie juste que ça ne crash pas
    matches = search("xxx", long_pattern)
    # tronqué à 200 chars, regex ``x{200}`` ne match pas → 0 matches
    assert isinstance(matches, list)


def test_search_empty_pattern_capped_by_hard_max() -> None:
    """Empty pattern compile et matche à chaque position. Le hard cap
    sur ``max_matches`` (``SEARCH_HARD_MAX_MATCHES = 30``) doit borner
    l'output même quand l'agent envoie un grand ``max_matches``.

    Test resserré (review S09.5 F8) : on utilise une chaîne assez
    longue (1 000 chars → 1 001 positions matchables) pour vraiment
    saturer le cap et prouver qu'il déclenche.
    """
    matches = search("x" * 1_000, "", max_matches=999)
    # Cap dur 30 — exactement, pas "<= 30" qui passerait trivialement
    # avec une chaîne courte.
    assert len(matches) == 30


# ---------------------------------------------------------------------------
# Constantes — sanity
# ---------------------------------------------------------------------------


def test_constants_sensible() -> None:
    """Sanity sur les seuils calibrés dans la story §"Données factuelles"."""
    assert OFFLOAD_THRESHOLD_CHARS == 12_000
    assert INDEX_BUDGET_CHARS == 4_000
    assert LOOKUP_MAX_CHARS_DEFAULT == 8_000


# ---------------------------------------------------------------------------
# Genericité — pas de logique métier Pappers
# ---------------------------------------------------------------------------


def test_module_contains_no_pappers_business_terms() -> None:
    """Garde-fou meta : le module doit rester strictement générique
    (cf. check-list S09.5 phase 3). Aucun champ MCP-spécifique en dur,
    aucune mention business — sinon refactoriser."""
    import inspect as _inspect

    from genial_agent import payload_vault as pv

    src = _inspect.getsource(pv).lower()
    # Liste exacte de la check-list S09.5 phase 3.
    forbidden = ("pappers", "siren", "comptes", "bilan", "dirigeant")
    for term in forbidden:
        assert term not in src, f"terme métier interdit dans payload_vault.py : {term!r}"
