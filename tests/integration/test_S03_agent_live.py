"""Tests d'intégration S03 — live contre Anthropic + MCP Pappers.

Skip explicite si ``ANTHROPIC_API_KEY`` **ou** ``PAPPERS_API_KEY`` est
absent. Chaque test consomme ~1–3 crédits Pappers.

Ne pas utiliser ``informations-entreprise`` : outil Premium Pappers
(probe S02 2026-04-24), indisponible sur le pack API offert. On passe
par ``sirenisateur`` (résolution nom→SIREN) + ``recherche-entreprises``
pour valider U1.
"""

from __future__ import annotations

import os
import re

import pytest

from genial_agent.agent import ConversationState, run_turn
from genial_agent.models import ModelTier

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))
REASON = "ANTHROPIC_API_KEY and/or PAPPERS_API_KEY not set"


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_fiche_lvmh_contains_siren() -> None:
    """LVMH SIREN = 775670417. Le test passe si l'agent retourne ce
    SIREN dans le texte final, et si au moins 1 tool call a été
    effectué."""
    state = ConversationState()
    text_chunks: list[str] = []
    llm_meta_events: list[dict] = []
    tool_use_events: list[dict] = []
    end_event: dict | None = None

    async for event in run_turn(
        state,
        "Donne-moi la fiche de LVMH",
        tier=ModelTier.HAIKU,
    ):
        if event["type"] == "text":
            text_chunks.append(event["content"])
        elif event["type"] == "tool_use":
            tool_use_events.append(event)
        elif event["type"] == "llm_meta":
            llm_meta_events.append(event)
        elif event["type"] == "end":
            end_event = event

    full_text = "".join(text_chunks)
    # Claude restitue parfois le SIREN en groupes (775 670 417), parfois
    # en bloc (775670417). On normalise pour matcher les deux formats.
    digits_only = re.sub(r"\D", "", full_text)
    assert "775670417" in digits_only, f"SIREN LVMH absent : {full_text!r}"
    assert state.tool_calls_count >= 1
    assert len(tool_use_events) >= 1
    assert len(llm_meta_events) >= 1
    # Vérif que le llm_meta contient bien les champs stats S07.
    meta = llm_meta_events[0]
    assert {
        "model",
        "input_tokens",
        "output_tokens",
        "latency_ms",
        "stop_reason",
        "request_id",
    } <= meta.keys()
    assert meta["input_tokens"] > 0
    assert meta["output_tokens"] > 0
    assert meta["latency_ms"] >= 0
    assert end_event is not None


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_refus_hors_scope_apple() -> None:
    """Question sur Apple (entreprise US) → refus scope FR, pas
    d'hallucination de SIREN US.

    Critère de réussite (review S03 A7) : **aucun SIREN inventé** dans
    la réponse (l'agent a le droit d'appeler un tool pour vérifier que
    "Apple Inc" n'est pas dans Pappers, mais pas de fabriquer un SIREN),
    ET le refus est explicite par au moins un mot-clé parmi une liste
    étendue (français/france/pappers/étrangèr/american/périmètr/scope).
    Ce test est tolérant à la formulation Haiku tout en rejetant une
    hallucination pure.
    """
    state = ConversationState()
    text_chunks: list[str] = []
    tool_calls = 0

    async for event in run_turn(state, "Donne-moi la fiche d'Apple Inc", tier=ModelTier.HAIKU):
        if event["type"] == "text":
            text_chunks.append(event["content"])
        elif event["type"] == "tool_use":
            tool_calls += 1

    full_text = "".join(text_chunks)
    lower = full_text.lower()

    # 1. Au moins un marqueur de cadrage scope : vocabulaire large mais
    #    cohérent avec un refus FR-only. Réduit la flakiness Haiku vs
    #    formulation exacte.
    scope_markers = (
        "français",
        "france",
        "pappers",
        "étrangèr",
        "américain",
        "us ",
        "périmètr",
        "scope",
        "pas couvert",
        "non couvert",
    )
    assert any(k in lower for k in scope_markers), (
        f"Aucun marqueur de cadrage scope dans la réponse : {full_text!r}"
    )

    # 2. Aucun SIREN fabriqué (9 chiffres consécutifs) ne doit apparaître.
    #    Apple n'a pas de SIREN français ; si l'agent en cite un, c'est
    #    une hallucination pure. On accepte les digits groupés (XXX XXX XXX)
    #    en normalisant.
    digits_only = re.sub(r"\D", "", full_text)
    assert not re.search(r"\d{9}", digits_only), (
        f"SIREN halluciné sur entreprise non-FR : {full_text!r}"
    )


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_multi_turn_pronoun_resolution_lvmh() -> None:
    """Tour 1 : fiche LVMH. Tour 2 : "ses dirigeants" — l'agent doit
    résoudre "ses" sur LVMH (SIREN 775670417) et interroger Pappers
    sur cette même entité.

    Critère de réussite : au moins un tool call du tour 2 référence
    LVMH, soit par SIREN (``775670417``) soit par nom (``LVMH``). Le
    contenu exact (noms de dirigeants) dépend de la donnée Pappers
    disponible et n'est pas asserté — on valide la **résolution du
    pronom**, pas la complétude de la base Pappers.
    """
    state = ConversationState()
    async for _ in run_turn(state, "Donne-moi la fiche de LVMH", tier=ModelTier.HAIKU):
        pass

    text_chunks: list[str] = []
    tool_use_events: list[dict] = []
    async for event in run_turn(state, "Qui sont ses dirigeants ?", tier=ModelTier.HAIKU):
        if event["type"] == "text":
            text_chunks.append(event["content"])
        elif event["type"] == "tool_use":
            tool_use_events.append(event)

    # Au moins un tool call doit référencer LVMH — preuve que "ses" a
    # bien été résolu sur l'entité active du tour 1.
    assert tool_use_events, "le 2e tour n'a déclenché aucun tool call"
    tool_targets = " ".join(str(ev["input"]) for ev in tool_use_events).lower()
    assert "775670417" in re.sub(r"\D", "", tool_targets) or "lvmh" in tool_targets, (
        f"Aucun tool call ne cible LVMH au tour 2 : {tool_use_events!r}"
    )

    # La réponse doit au moins parler de LVMH / dirigeants pour prouver
    # que l'agent reste cohérent avec le contexte.
    full = "".join(text_chunks).lower()
    assert "lvmh" in full or "dirigean" in full, f"Réponse hors-sujet : {full!r}"


@pytest.mark.skipif(SKIP, reason=REASON)
async def test_end_event_emitted_with_tool_count() -> None:
    """Garantit qu'un event ``end`` est toujours émis en fin de turn,
    avec un ``tool_calls_count`` cohérent avec le state et le nombre
    d'events ``tool_use`` observés."""
    state = ConversationState()
    end_event: dict | None = None
    tool_use_count = 0

    async for event in run_turn(state, "Donne-moi la fiche de LVMH", tier=ModelTier.HAIKU):
        if event["type"] == "tool_use":
            tool_use_count += 1
        elif event["type"] == "end":
            end_event = event

    assert end_event is not None
    assert end_event["tool_calls_count"] == tool_use_count == state.tool_calls_count
    assert end_event["reason"] in {
        "end_turn",
        "max_tokens",
        "refusal",
        "pause_turn",
        "stop_sequence",
    }
