"""Tests d'intégration S04 — live contre Anthropic + MCP Pappers.

Couverture live limitée aux cas déterministes :

- Simple → Haiku (garantie par le keyword router).
- Keyword complexe → Sonnet direct (garantie par le keyword router).

Les scénarios d'escalation (self / forced) sont couverts en unit via
fake AsyncAnthropic — Haiku en live peut ne pas escalader
systématiquement, c'est normal (sa métacognition est imparfaite,
c'est pour ça qu'on a aussi le cap forced + le keyword router).
"""

from __future__ import annotations

import os

import pytest

from genial_agent.agent import ConversationState
from genial_agent.models import MODEL_HAIKU, MODEL_SONNET
from genial_agent.routing import run_routed_turn

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))
REASON = "ANTHROPIC_API_KEY and/or PAPPERS_API_KEY not set"


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_simple_stays_haiku_live() -> None:
    """U1 simple → Haiku tier initial. Le test valide le **keyword
    router** (default → Haiku), pas l'absence d'escalade forced.

    Le cap ``WALL_CLOCK_S`` (cahier §5.3) est un filet de sécurité
    backend qui peut se déclencher si l'env (latence Pappers +
    Anthropic global) dépasse 15 s cumulés sur quelques tool calls.
    Quand c'est le cas, S04 escalade Haiku → Sonnet en mode ``forced``
    (``cap_wall_clock``) — c'est un comportement *positif* qui prouve
    que le filet fonctionne. Cf. review S05 §I-1.

    Contrats vérifiés :
    - ``routing_initial.tier == "haiku"`` (keyword router OK).
    - ``routing_done`` toujours émis.
    - Si pas d'escalade : tous les ``llm_meta`` sont en Haiku.
    - Si escalade : elle est ``forced`` via ``cap_wall_clock`` ou
      ``cap_tool_calls_per_turn`` (jamais ``self`` sur fiche LVMH
      simple, l'auto-escalade Haiku serait suspecte).
    """
    state = ConversationState()
    routing_initial: dict | None = None
    routing_done: dict | None = None
    llm_models: list[str] = []

    async for event in run_routed_turn(state, "Donne-moi la fiche de LVMH"):
        if event["type"] == "routing_initial":
            routing_initial = event
        elif event["type"] == "routing_done":
            routing_done = event
        elif event["type"] == "llm_meta":
            llm_models.append(event["model"])

    # Contrat dur : routing initial est Haiku (keyword router OK).
    assert routing_initial is not None
    assert routing_initial["tier"] == "haiku"

    # Contrat dur : routing_done émis (filet S04 release `state.lock`).
    assert routing_done is not None

    # Contrat soft : pas d'escalade dans le cas idéal. Si l'env
    # déclenche un cap_wall_clock ou cap_tool_calls, on accepte mais
    # on vérifie que l'escalade est ``forced`` (jamais ``self``).
    if routing_done["escalated"]:
        assert routing_done["escalation_mode"] == "forced", (
            f"escalade auto Haiku→Sonnet sur fiche LVMH simple inattendue : {routing_done!r}"
        )
        assert routing_done["escalation_reason_code"] in {
            "cap_wall_clock",
            "cap_tool_calls_per_turn",
        }, f"reason_code inattendu : {routing_done!r}"
    else:
        # Cas idéal : pas d'escalade → tous les llm_meta sont Haiku
        # (pas de bascule silencieuse).
        assert routing_done["model_used"] == "haiku"
        assert all(m == MODEL_HAIKU for m in llm_models)


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_complex_keyword_goes_sonnet_live() -> None:
    """Le keyword 'Compare' déclenche Sonnet direct — zéro appel Haiku."""
    state = ConversationState()
    routing_initial: dict | None = None
    routing_done: dict | None = None
    llm_models: list[str] = []

    async for event in run_routed_turn(state, "Compare LVMH et Kering sur 3 ans"):
        if event["type"] == "routing_initial":
            routing_initial = event
        elif event["type"] == "routing_done":
            routing_done = event
        elif event["type"] == "llm_meta":
            llm_models.append(event["model"])

    assert routing_initial is not None
    assert routing_initial["tier"] == "sonnet"
    assert routing_initial["reason"] == "keyword"

    assert routing_done is not None
    assert routing_done["model_used"] == "sonnet"
    assert routing_done["escalated"] is False

    # Tous les llm_meta sont en Sonnet
    assert all(m == MODEL_SONNET for m in llm_models)
