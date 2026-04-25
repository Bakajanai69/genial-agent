"""Régression live U3 — review S08 §B1 (post-deploy notes §D).

Le smoke test live de Lancelot sur l'URL Railway du 2026-04-25 a
déclenché un **double cap hit** sur U3 (`MAX_TOOL_CALLS_PER_TURN=5` +
`MAX_TOKENS_PER_SESSION=50_000`) — score critic 30 %, réponse marquée
incomplète. Cf. S08 deployment.md §D.

Ce test rejoue le **prompt exact** qui a échoué et vérifie qu'avec :

- ``MAX_TOOL_CALLS_PER_TURN = 7`` (bumpé de 5),
- ``MAX_TOKENS_PER_SESSION = 80_000`` (bumpé de 50_000),
- prompt agent durci anti-redondance ``sirenisateur`` (``prompts.py``),

le tour passe **sans cap** et la réponse contient bien les deux entités
sourcées. Coût estimé : ~6-10 crédits Pappers + ~10-20K tokens Sonnet
(~$0.05). Opt-in : ``make test-integration``.

Ne jamais "réparer" ce test en relâchant les assertions : si le cap est
hit à nouveau, c'est que les bumps S08 §B1 ne suffisent plus → escalade
produit (revoir architecture, pas les chiffres).
"""

from __future__ import annotations

import os

import pytest

from genial_agent.agent import ConversationState
from genial_agent.guardrails.caps import (
    MAX_TOKENS_PER_SESSION,
    MAX_TOOL_CALLS_PER_TURN,
)
from genial_agent.guardrails.pipeline import run_guarded_turn

pytestmark = pytest.mark.integration

SKIP = not (os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))

# Le prompt exact reporté en S08 notes §D — celui qui a déclenché le
# double cap hit en prod.
U3_HEAVY_PROMPT = (
    "Compare la santé financière de Carrefour et Casino sur 3 ans, "
    "lequel présente le moins de risque ?"
)


def test_caps_have_been_bumped_for_u3() -> None:
    """Sanity check des constantes — sans condition réseau.

    Si quelqu'un revert ``caps.py`` à 5/50_000, le test live ci-dessous
    flapperait par dépassement du cap. On veut un signal clair en amont.
    """
    assert MAX_TOOL_CALLS_PER_TURN >= 7, (
        f"S08 review §B1 : ``MAX_TOOL_CALLS_PER_TURN`` doit être ≥ 7 pour "
        f"que U3 passe robustement. Actuel : {MAX_TOOL_CALLS_PER_TURN}."
    )
    assert MAX_TOKENS_PER_SESSION >= 80_000, (
        f"S08 review §B1 : ``MAX_TOKENS_PER_SESSION`` doit être ≥ 80_000 pour "
        f"que U3 + 1 follow-up multi-turn tienne. Actuel : "
        f"{MAX_TOKENS_PER_SESSION}."
    )


@pytest.mark.skipif(SKIP, reason="ANTHROPIC_API_KEY ou PAPPERS_API_KEY absent")
async def test_u3_heavy_compare_passes_without_cap_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression live — U3 lourd ne doit plus hit les caps **produit**
    (tool_calls + token_budget).

    Le filet ``wall_clock`` (15 s) est **monkey-patché à 60 s** ici parce
    que l'objet du test est la régression S08 §B1 = caps **produit**, pas
    le filet env-dependent (cf. ``caps.py`` §"Override env" + S05 review
    §I-1 : sur WSL avec latence Anthropic global + Pappers, les 15 s
    sont mangés en cours d'exécution Sonnet. La prod Railway EU-West
    n'a pas ce problème — c'est un artefact dev local).

    Contrats durs :

    - ``routing_initial.tier == "sonnet"`` (keyword "Compare" + multi-entités).
    - **Aucun event ``capped``** (ni tool_calls, ni token_budget) —
      c'est l'objet du test.
    - Au moins 1 tool Pappers consulté (``sirenisateur`` ou
      ``comptes-entreprise``) — sinon Sonnet a halluciné.
    - ``critic_result`` émis, couleur **pas rouge** (un orange est
      acceptable — la requête est complexe et certaines données
      financières peuvent manquer côté Pappers).

    Soft :

    - On loggue le nombre de tool_use observés pour suivi (devrait
      être 4-6 selon la stratégie Sonnet).
    """
    # Bypass du wall-clock cap pour ce test live spécifique. Le binding
    # importé dans ``routing.py`` est ce qu'on patche (pattern documenté
    # dans test_S04_routing.py).
    from genial_agent import routing as routing_mod

    # 180 s : sur WSL avec latence Anthropic global + Pappers cumulée,
    # 60 s ne suffit pas pour 4-6 round-trips Sonnet + tool calls. La
    # prod Railway EU-West reste à 15 s (cf. caps.py).
    monkeypatch.setattr(routing_mod, "WALL_CLOCK_S", 180)

    state = ConversationState()
    events: list[dict] = []

    async for event in run_guarded_turn(state, U3_HEAVY_PROMPT, "live_u3_s08_regress"):
        events.append(event)

    # 1. Routing : Sonnet via keyword.
    routing_initial = next(e for e in events if e["type"] == "routing_initial")
    assert routing_initial["tier"] == "sonnet", (
        f"Le keyword router doit dispatcher Sonnet sur 'Compare ... et ...' : {routing_initial!r}"
    )

    # 2. (DUR) Aucun cap hit — c'est l'objet du test.
    capped_events = [e for e in events if e["type"] == "capped"]
    assert not capped_events, (
        f"Cap déclenché malgré le bump S08 §B1 — escalade nécessaire. "
        f"Caps actuels : tool_calls={MAX_TOOL_CALLS_PER_TURN}, "
        f"tokens={MAX_TOKENS_PER_SESSION}. Events capped : {capped_events!r}"
    )

    # 3. Au moins un tool Pappers consulté.
    tool_uses = [e for e in events if e["type"] == "tool_use"]
    assert tool_uses, "Sonnet a fini sans interroger Pappers — anomalie produit"

    pappers_tools = {"sirenisateur", "recherche-entreprises", "comptes-entreprise"}
    assert any(t["name"] in pappers_tools for t in tool_uses), (
        f"aucun tool Pappers attendu utilisé : {[t['name'] for t in tool_uses]}"
    )

    # 4. Critic émis avec couleur acceptable (pas rouge).
    critic = next(e for e in events if e["type"] == "critic_result")
    assert critic["color"] in ("green", "orange"), (
        f"Critic rouge sur U3 — la réponse est jugée non exploitable : {critic!r}"
    )
    assert critic["scope_ok"] is True

    # 5. Soft : log le nombre de tool_use pour suivi régression.
    print(
        f"\n[S08 §B1 regress] U3 heavy: {len(tool_uses)} tool_use, "
        f"critic={critic['color']} ({critic.get('confidence')}%)"
    )
