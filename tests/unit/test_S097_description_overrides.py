"""S09.7 Axe 4bis — DESCRIPTION_OVERRIDES côté MCP schema.

La carte des tools (anciennement injectée dans le system prompt) est
migrée dans les ``description`` des tools MCP eux-mêmes via le mapping
``DESCRIPTION_OVERRIDES``. Le pattern Anthropic standard est qu'un tool
soit auto-documenté par son schéma : l'agent lit la description en
contexte de tool selection sans qu'on injecte de règles transversales
dans le system prompt.

**Borne stricte** : uniquement des clarifications de schéma input/output
que Pappers aurait dû documenter. Pas de logique métier (pas de SIREN
spécifique, pas de "pour LVMH fais X").
"""

from __future__ import annotations

from genial_agent.mcp_pappers import (
    DESCRIPTION_OVERRIDES,
    PappersTool,
    to_anthropic_schema,
)


def test_overrides_present_for_main_tools() -> None:
    """Les tools clés du MVP ont une override documentée.

    Liste figée par la story §"Axe 4bis". Ajouts/retraits = changement
    de contrat documentation, à passer en review explicite.
    """
    expected_keys = {
        "sirenisateur",
        "recherche-entreprises",
        "comptes-entreprise",
        "cartographie-entreprise",
        "recherche-dirigeants",
    }
    assert expected_keys <= set(DESCRIPTION_OVERRIDES.keys())


def test_overrides_appended_to_description() -> None:
    """``to_anthropic_schema`` concatène l'override en suffixe à la
    description originale Pappers — n'écrase pas l'existant."""
    pt = PappersTool(
        name="recherche-entreprises",
        description="Recherche d'entreprises françaises par critères.",
        input_schema={"type": "object"},
    )
    [out] = to_anthropic_schema([pt])
    assert out["description"].startswith("Recherche d'entreprises françaises par critères.")
    assert DESCRIPTION_OVERRIDES["recherche-entreprises"] in out["description"]


def test_overrides_no_effect_on_unknown_tool() -> None:
    """Un tool sans override garde sa description originale inchangée."""
    pt = PappersTool(
        name="some-unknown-tool",
        description="Description originale.",
        input_schema={"type": "object"},
    )
    [out] = to_anthropic_schema([pt])
    assert out["description"] == "Description originale."


def test_overrides_dont_touch_input_schema() -> None:
    """L'override n'altère JAMAIS ``input_schema`` — les contraintes
    Anthropic ``input_schema`` doivent rester celles du MCP brut."""
    schema = {
        "type": "object",
        "properties": {"siren": {"type": "string"}},
        "required": ["siren"],
    }
    pt = PappersTool(
        name="comptes-entreprise",
        description="Comptes annuels.",
        input_schema=schema,
    )
    [out] = to_anthropic_schema([pt])
    assert out["input_schema"] == schema


def test_overrides_no_business_logic_leaks() -> None:
    """Borne stricte : pas de logique métier (SIREN spécifiques,
    "pour LVMH fais X") dans les overrides. Liste de termes interdits
    explicite — toute violation = retour vers le system prompt pour
    discussion.

    Tolérés : noms de tools, noms de champs JSON ("annee_finances"),
    coût en crédits, distinctions input vs return_fields.
    """
    forbidden_terms = ("LVMH", "BNP", "Carrefour", "Casino", "Arnault")
    forbidden_sirens = ("775670417", "552032534", "451321335", "554501171")
    for tool_name, override in DESCRIPTION_OVERRIDES.items():
        for term in forbidden_terms:
            assert term not in override, (
                f"override de {tool_name!r} contient le terme métier {term!r}"
            )
        for siren in forbidden_sirens:
            assert siren not in override, (
                f"override de {tool_name!r} contient un SIREN figé {siren!r}"
            )


def test_overrides_mention_clarifications_useful_to_llm() -> None:
    """Spot-check : l'override de ``recherche-entreprises`` mentionne
    bien les ``return_fields`` (la principale source de confusion LLM
    historique) et l'override de ``comptes-entreprise`` mentionne le bug
    PAYG (workaround_hint)."""
    re_override = DESCRIPTION_OVERRIDES["recherche-entreprises"]
    assert "return_fields" in re_override

    ce_override = DESCRIPTION_OVERRIDES["comptes-entreprise"]
    assert "workaround_hint" in ce_override.lower() or "payg" in ce_override.lower()


def test_to_anthropic_schema_keeps_3_required_keys() -> None:
    """Non-régression S02 : la shape exacte reste
    ``{name, description, input_schema}`` — l'override n'ajoute pas de
    clé."""
    pt = PappersTool(
        name="sirenisateur",
        description="Trouve un SIREN.",
        input_schema={"type": "object"},
    )
    [out] = to_anthropic_schema([pt])
    assert set(out.keys()) == {"name", "description", "input_schema"}
