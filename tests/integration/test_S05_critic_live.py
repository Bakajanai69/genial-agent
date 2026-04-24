"""Tests d'intégration S05 — Haiku-critic live (cahier §14.3 C6).

2 tests opt-in (marker ``integration``), exécutés uniquement via
``make test-integration``. Consomme ~200-500 tokens Haiku par test
(~$0.0002 par test). Skip si ``ANTHROPIC_API_KEY`` absent.

Les autres garde-fous (input_gate, output_validator, sirens, PII,
token_budget, pipeline E2E via fake AsyncAnthropic) sont déterministes
et testés en unit — pas besoin de live.

Cf. README "Décisions de cohérence" §6 : les live de S02/S03/S05 ne
sont **pas** rejoués par les dev/review agents des stories suivantes
(S06+) — consomme des crédits sans value add.
"""

from __future__ import annotations

import os

import pytest

from genial_agent.guardrails.critic import critique_async

pytestmark = pytest.mark.integration
SKIP = not os.getenv("ANTHROPIC_API_KEY")


@pytest.mark.skipif(SKIP, reason="ANTHROPIC_API_KEY not set")
async def test_critic_on_clean_response() -> None:
    """Une réponse propre, sourcée, horodatée → Haiku doit donner un
    signal positif (scope_ok=True, hallucination_risk bas, couleur
    green ou orange)."""
    result = await critique_async(
        question="Donne-moi la fiche de LVMH",
        response=(
            "LVMH est un groupe de luxe français. SIREN 775670417, siège "
            "22 avenue Montaigne à Paris. CA 94,1 Md€ (bilan clos 31/12/2023)."
        ),
    )
    assert result.scope_ok is True
    assert result.hallucination_risk in ("low", "medium")
    # Pas de rouge sur une réponse propre — vert ou orange tolérés.
    assert result.color in ("green", "orange")


@pytest.mark.skipif(SKIP, reason="ANTHROPIC_API_KEY not set")
async def test_critic_on_advisory_response() -> None:
    """Une réponse prescriptive ("je te conseille d'investir") → Haiku
    doit détecter le ton advisory et flag advisory_language=True. La
    couleur n'est pas verte."""
    result = await critique_async(
        question="Que penser de LVMH ?",
        response=(
            "LVMH est un excellent placement. Je te conseille vivement "
            "d'investir massivement, c'est une valeur à acheter absolument."
        ),
    )
    assert result.advisory_language is True
    assert result.color in ("orange", "red")
