"""Tests unitaires du mapping PappersTool → Anthropic tools schema.

Contrats vérifiés :

- Shape exacte : ``{name, description, input_schema}`` (snake_case).
- Compatibilité avec le pattern Anthropic ``^[a-zA-Z0-9_-]{1,128}$`` sur
  tous les noms ``RETAINED_TOOLS`` (kebab-case de Pappers).
- Pont ``inputSchema`` (camelCase MCP) → ``input_schema`` (snake_case
  Anthropic) sans perte de contenu (dont ``$schema`` draft 2020-12).
- Compat ``anthropic.types.ToolParam`` (TypedDict) — review R4 : on
  valide maintenant la shape côté SDK Anthropic, plus juste les clés.
"""

from __future__ import annotations

import re

from anthropic.types import ToolParam
from mcp.types import Tool as McpTool

from genial_agent.mcp_pappers import (
    RETAINED_TOOLS,
    PappersTool,
    to_anthropic_schema,
)


def test_mapping_minimal_shape() -> None:
    """Exemple avec un outil effectivement retenu (review R3 : avant le
    fix, l'exemple citait ``informations-entreprise`` qui est justement
    exclu de ``RETAINED_TOOLS`` — incohérent pour un lecteur froid)."""
    tools = [
        PappersTool(
            name="recherche-entreprises",
            description="Recherche d'entreprises françaises par critères",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ]
    out = to_anthropic_schema(tools)
    assert out == [
        {
            "name": "recherche-entreprises",
            "description": "Recherche d'entreprises françaises par critères",
            "input_schema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }
    ]


def test_mapping_preserves_order() -> None:
    tools = [
        PappersTool(name="a-b", description="d1", input_schema={"type": "object"}),
        PappersTool(name="c-d", description="d2", input_schema={"type": "object"}),
    ]
    out = to_anthropic_schema(tools)
    assert [t["name"] for t in out] == ["a-b", "c-d"]


def test_anthropic_tool_name_pattern() -> None:
    """Tous les noms Pappers kebab-case doivent matcher le pattern
    Anthropic ``^[a-zA-Z0-9_-]{1,128}$``."""
    pattern = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
    for name in RETAINED_TOOLS:
        assert pattern.match(name), f"Tool name invalide pour Anthropic: {name!r}"


def test_camel_case_input_schema_bridge() -> None:
    """Vérifie que la clé passe bien de camelCase (spec MCP) à
    snake_case (Anthropic) sans écrasement du contenu JSON Schema."""
    mcp_tool = McpTool(
        name="sirenisateur",
        description="Trouve un SIREN depuis un nom",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "$schema": "https://json-schema.org/draft/2020-12/schema",
        },
    )
    # Conversion (simule `list_available_tools`)
    pt = PappersTool(
        name=mcp_tool.name,
        description=mcp_tool.description or "",
        input_schema=mcp_tool.inputSchema,
    )
    [out] = to_anthropic_schema([pt])
    assert "input_schema" in out
    assert "inputSchema" not in out
    # Le $schema est conservé, Anthropic l'accepte sans strip.
    assert out["input_schema"]["$schema"].endswith("2020-12/schema")


def test_mapping_empty_list_returns_empty() -> None:
    assert to_anthropic_schema([]) == []


def test_mapping_exact_keys_no_extra() -> None:
    tools = [PappersTool(name="x-y", description="d", input_schema={"type": "object"})]
    [out] = to_anthropic_schema(tools)
    assert set(out.keys()) == {"name", "description", "input_schema"}


def test_mapping_is_typeddict_compatible() -> None:
    """Review R4 : le payload doit être directement accepté comme
    ``anthropic.types.ToolParam`` (TypedDict). On construit le dict et
    on le passe à la factory TypedDict — pas juste un check de clés.
    """
    pt = PappersTool(
        name="recherche-entreprises",
        description="Recherche entreprises",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )
    [raw] = to_anthropic_schema([pt])
    # TypedDict n'a pas de validation runtime mais on vérifie que la
    # construction par keyword args ne lève pas de TypeError et que
    # toutes les clés Required sont présentes.
    param: ToolParam = ToolParam(
        name=raw["name"],
        description=raw["description"],
        input_schema=raw["input_schema"],
    )
    assert param["name"] == "recherche-entreprises"
    assert param["input_schema"]["type"] == "object"
