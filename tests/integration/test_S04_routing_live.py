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

    assert routing_initial is not None
    assert routing_initial["tier"] == "haiku"

    assert routing_done is not None
    assert routing_done["model_used"] == "haiku"
    assert routing_done["escalated"] is False

    # Tous les llm_meta sont en Haiku (pas de bascule silencieuse)
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
