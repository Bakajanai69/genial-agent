"""3 sessions Chainlit en parallèle — vérifie l'isolation par session.

Smoke test du cahier §13 / §17.4 ("Test concurrent 3 onglets OK avant
push"). On lance 3 ``run_guarded_turn`` en parallèle avec 3
``session_id`` distincts et 3 prompts (LVMH / BNP / Carrefour).

Assertions :

- Aucun ``capped`` (pas de cap déclenché par contention).
- Chaque ``ConversationState.messages`` contient bien le prompt user
  qui lui était destiné, **pas** un autre.
- Pas de fuite d'idempotence cross-session (différente clef
  ``session_id || sha256(msg)`` cf. ``observability/idempotence.py``).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from genial_agent.agent import ConversationState
from genial_agent.guardrails.pipeline import run_guarded_turn
from genial_agent.observability.idempotence import IdempotenceCache

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))


async def _drain(
    state: ConversationState,
    prompt: str,
    session_id: str,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    async for event in run_guarded_turn(state, prompt, session_id):
        events.append(event)
    return events


@pytest.mark.skipif(SKIP, reason="ANTHROPIC_API_KEY ou PAPPERS_API_KEY absent")
async def test_3_concurrent_sessions_stay_isolated() -> None:
    states = [ConversationState() for _ in range(3)]
    prompts = [
        "Donne-moi la fiche de LVMH",
        "Qui sont les dirigeants actuels de BNP Paribas selon Pappers ?",
        "Quel est le dernier chiffre d'affaires de Carrefour ?",
    ]
    session_ids = ["s09_conc_lvmh", "s09_conc_bnp", "s09_conc_carrefour"]

    runs = await asyncio.gather(
        *(
            _drain(state, prompt, sid)
            for state, prompt, sid in zip(states, prompts, session_ids, strict=True)
        )
    )

    # 1. Aucun cap déclenché par contention.
    for sid, events in zip(session_ids, runs, strict=True):
        capped = [e for e in events if e["type"] == "capped"]
        assert not capped, f"{sid} a déclenché un cap : {capped!r}"

    # 2. Chaque state n'a vu QUE son propre user message (premier
    #    message ``user`` du historique, wrappé par S03 dans
    #    ``<user_input>...</user_input>``).
    expected_keywords = ["LVMH", "BNP", "Carrefour"]
    for state, expected in zip(states, expected_keywords, strict=True):
        user_msgs = [m for m in state.messages if m.get("role") == "user"]
        assert user_msgs, f"state pour {expected} n'a pas de user message"
        first_user = user_msgs[0]
        content = first_user.get("content")
        if isinstance(content, str):
            assert expected in content, (
                f"contamination : attendu {expected!r}, vu {content[:200]!r}"
            )

    # 3. Aucun crossover : le premier user message d'un state ne doit
    #    pas contenir un keyword d'un autre state.
    for state, expected in zip(states, expected_keywords, strict=True):
        first_user = next(m for m in state.messages if m.get("role") == "user")
        content = first_user.get("content", "")
        if isinstance(content, str):
            for other in expected_keywords:
                if other == expected:
                    continue
                assert other not in content, f"crossover : state {expected} contient {other}"

    # 4. Pas de fuite d'idempotence cross-session — promesse de la
    #    docstring (review S09 §T-5). Le cache idempotence est peuplé
    #    par ``app.py``, pas par ``run_guarded_turn`` directement, donc
    #    on ne peut pas observer un cache hit cross-session via le
    #    pipeline ici. On vérifie le contrat **par construction** : la
    #    clé ``IdempotenceCache.key`` est préfixée par ``session_id``,
    #    donc deux sessions différentes avec le même message produisent
    #    deux clés distinctes — impossible de servir la réponse de l'une
    #    à l'autre.
    same_msg = "Donne-moi la fiche de LVMH"
    key_a = IdempotenceCache.key(session_ids[0], same_msg)
    key_b = IdempotenceCache.key(session_ids[1], same_msg)
    assert key_a != key_b, f"clé idempotence identique cross-session : {key_a!r}"
    assert key_a.startswith(f"{session_ids[0]}:"), (
        f"clé {key_a!r} ne commence pas par session_id préfixe"
    )
    assert key_b.startswith(f"{session_ids[1]}:"), (
        f"clé {key_b!r} ne commence pas par session_id préfixe"
    )
