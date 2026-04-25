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

**Bumps successifs WALL_CLOCK_S** (review S08 §B1bis post smoke prod) :

- 15 → 30 s (1ère itération) : le cap 15 s coupait Sonnet en début
  de streaming round 3.
- 30 → 60 s (2ème itération, post-test webapp) : le 30 s flap encore
  parce que **Anthropic prompt caching n'est PAS activé** côté
  ``agent.py``. Conséquence : à chaque round, l'API re-tokenize tout
  le contexte cumulé (system prompt + tools + N rounds × messages),
  ce qui fait exploser le TTFT (Time To First Token) sur le 3ème
  round U3 où le contexte cumulé atteint ~50-60 K tokens (sortie
  Pappers ``comptes-entreprise`` × 2 entités × 3 ans). User
  observation : "fail silencieux car après le dernier
  comptes-entreprise l'agent freeze, rien logg jusqu'au timeout".
  60 s couvre le pire cas observé (TTFT lourd + streaming complet).
  **Vrai fix produit** : activer Anthropic prompt caching (système
  + tools + messages N-1) — listé en next-step S09. Coupe TTFT 5-10×.
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
# Bumps successifs post-deploy (review S08 §B1bis, 2 itérations) :
# 15 → 30 → 60 s. La 2ème bump est venue d'un fail silencieux
# observé en webapp prod : agent freeze 30 s après le dernier
# tool_result avant que le cap 30 s firefires, sans ton/log
# intermédiaire. Cause racine : pas de prompt caching Anthropic
# dans ``agent.py`` → TTFT round 3 sur 50-60 K tokens cumulés est
# trop lent. 60 s couvre le pire cas observé. Le vrai fix produit
# (prompt caching) est listé en next-step S09 — il couperait le
# TTFT 5-10× et permettrait de revenir à 30 s.
def _resolve_wall_clock_s(default: int = 60) -> int:
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
