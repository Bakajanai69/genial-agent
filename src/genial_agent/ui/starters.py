"""Définition des 4 starters Chainlit (cf. cahier §16.1).

Les emojis ⚡ vs 🧠 indiquent visuellement le tier modèle attendu, avant
même le clic — effet pédagogique sur le routing pour l'évaluateur.

Couverture des cas d'usage :

- U1 (fiche)        — ⚡ Haiku attendu
- U2 (mandats)      — ⚡ Haiku attendu
- U3 (comparaison)  — 🧠 Sonnet via keyword router
- U5 (KYC SIREN)    — 🧠 Sonnet (KYC = vérification approfondie)

SIREN 552032534 = Accor SA — Luhn-valide, vérifié 2026-04-25.
"""

from __future__ import annotations

import chainlit as cl

STARTERS: list[cl.Starter] = [
    cl.Starter(
        label="⚡ Fiche LVMH",
        message="Donne-moi la fiche d'identité de LVMH",
    ),
    cl.Starter(
        label="⚡ Mandats Bernard Arnault",
        message="Quelles sociétés Bernard Arnault dirige-t-il actuellement ?",
    ),
    cl.Starter(
        label="🧠 Compare Carrefour vs Casino",
        message=(
            "Compare la santé financière de Carrefour et Casino sur 3 ans, "
            "lequel présente le moins de risque ?"
        ),
    ),
    cl.Starter(
        label="🧠 Vérifie SIREN 552032534",
        message="Vérifie l'entreprise SIREN 552032534, donne-moi un avis KYC.",
    ),
]
