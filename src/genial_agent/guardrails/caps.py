"""Constantes des caps de sécurité — single source of truth.

Cohérent cahier §5.3, §14.3 C4, §17.2, §19.4 D7, §19.12.1 et README
"Décisions de cohérence" (cap tool calls = 7 — bump S08 review post
test live U3, cf. notes S08 §D et review S08 §B1).

Consommateurs :

- ``routing.py`` (S04) : ``MAX_TOOL_CALLS_PER_TURN`` et ``WALL_CLOCK_S``.
- ``observability/credit_guard.py`` (S07) : ``DAILY_PAPPERS_CREDITS_CAP``.
- ``guardrails/token_budget.py`` (S05) : ``MAX_TOKENS_PER_SESSION``.
- ``voice/brief.py`` (S10, stretch) : ``MAX_BRIEFS_PER_SESSION``.

Modification de ces valeurs = changement de contrat produit. Toujours
passer par un commit explicite (``refactor(caps): bump tool calls cap
to N``) pour que la revue remonte l'impact.

**Override env** : ``WALL_CLOCK_S`` peut être bumpé en dev via la
variable d'environnement ``WALL_CLOCK_S_OVERRIDE``. Cas d'usage :
machines dont la latence Pappers + Anthropic global cumulée dépasse
la valeur cahier et déclenchent en permanence des escalades
``cap_wall_clock`` sur les tests live (cf. review S05 §I-1).

**Bump 15 s → 30 s** (review S08 §B1bis, post smoke webapp prod) :
le cap initial 15 s coupait Sonnet en plein streaming sur U3 (lourd,
~25 K tokens de bilans à synthétiser). Cf. notes S08 §D2 pour les
mesures de timing et la justification produit (un cap dur reste
opportun, mais 15 s confond "agent stuck" et "réponse U3 légitime").
"""

from __future__ import annotations

import os

# Agent / routing (S03 + S04) — cahier §5.3, §14.3 C4.
# Valeur historique 5 (cahier §5.3 d'origine). Bumpée à 7 après le smoke
# test live U3 sur l'URL Railway (cf. S08 notes §D) : Sonnet a besoin de
# 2 sirenisateur + 2 comptes-entreprise + 1 marge pour la comparaison
# Carrefour vs Casino, soit 5 calls *strictement* — toute inefficacité
# (ex: doublon SIREN) faisait crasher le tour. 7 garde la philosophie
# "cap dur" (filet anti-budget) tout en laissant U3 + 1 follow-up
# multi-turn passer robustement. Le cahier §4 (qui mentionnait 10) a été
# aligné à 7 dans le commit S08 review post-deploy.
MAX_TOOL_CALLS_PER_TURN = 7


# Wall-clock cap par turn. Lu une seule fois au module-load via env :
# si ``WALL_CLOCK_S_OVERRIDE`` est défini et parse en int positif, il
# remplace la valeur cahier. Sinon défaut 30 s. Lecture unique pour
# garder le contrat ``routing.py`` (bind local du symbole importé) —
# si on a besoin de scrubber dynamiquement en test, on monkey-patch
# ``routing.WALL_CLOCK_S`` directement.
#
# Bump 15 → 30 s post-deploy (review S08 §B1bis) : le smoke U3 webapp
# montrait Sonnet cancellé en plein streaming alors qu'il avait déjà
# fait 4 tool calls valides (sirenisateur×2 ‖ comptes-entreprise×2).
# 15 s confondait "agent stuck" (à juste titre cap'er) et "réponse
# légitime sur 25 K tokens de bilans" (à laisser finir). 30 s reste
# bounded enough pour un filet de sécurité (un agent réellement bloqué
# ne pondrait pas 4 tool_use en 15 s) tout en couvrant l'UX U3.
def _resolve_wall_clock_s(default: int = 30) -> int:
    override = os.getenv("WALL_CLOCK_S_OVERRIDE")
    if not override:
        return default
    try:
        value = int(override)
    except ValueError:
        return default
    return value if value > 0 else default


WALL_CLOCK_S = _resolve_wall_clock_s()

# Budget tokens par session (S05) — cf. phase 1 elicitation §Token budget.
# Bumpé de 50_000 à 80_000 après le smoke test live U3 (S08 notes §D) :
# le tool ``comptes-entreprise`` retourne ~3 ans de bilans (5-10K tokens
# / appel) ; cumul rapide en U3 (system prompt durci ~2K + schémas 7
# tools ~3K + 4-5 tool results lourds ~25-35K + reasoning Sonnet) —
# 50K était hit dès le 1er tour. 80K offre 1 tour U3 complet + 1
# follow-up multi-turn ("et son CA ?") sans flap du cap.
MAX_TOKENS_PER_SESSION = 80_000

# Stretch vocal (S10) — cahier §19.4 D7
MAX_BRIEFS_PER_SESSION = 20

# Observability (S07) — cahier §17.2
DAILY_PAPPERS_CREDITS_CAP = 100
