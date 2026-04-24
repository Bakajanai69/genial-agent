"""Constantes des caps de sécurité — single source of truth.

Cohérent cahier §5.3, §14.3 C4, §17.2, §19.4 D7, §19.12.1 et README
"Décisions de cohérence" (cap tool calls = 5).

Consommateurs :

- ``routing.py`` (S04) : ``MAX_TOOL_CALLS_PER_TURN`` et ``WALL_CLOCK_S``.
- ``observability/credit_guard.py`` (S07) : ``DAILY_PAPPERS_CREDITS_CAP``.
- ``guardrails/token_budget.py`` (S05) : ``MAX_TOKENS_PER_SESSION``.
- ``voice/brief.py`` (S10, stretch) : ``MAX_BRIEFS_PER_SESSION``.

Modification de ces valeurs = changement de contrat produit. Toujours
passer par un commit explicite (``refactor(caps): bump tool calls cap
to N``) pour que la revue remonte l'impact.
"""

from __future__ import annotations

# Agent / routing (S03 + S04) — cahier §5.3, §14.3 C4
MAX_TOOL_CALLS_PER_TURN = 5
WALL_CLOCK_S = 15

# Budget tokens par session (S05) — cf. phase 1 elicitation §Token budget.
# ~3-5 tours U3 (comparaison 4-6 tool calls @ 8-15k tokens) sur une
# session de démo.
MAX_TOKENS_PER_SESSION = 50_000

# Stretch vocal (S10) — cahier §19.4 D7
MAX_BRIEFS_PER_SESSION = 20

# Observability (S07) — cahier §17.2
DAILY_PAPPERS_CREDITS_CAP = 100
