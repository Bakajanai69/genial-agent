"""Mode dégradé cache-only quand le cap crédits Pappers journalier est
atteint (cahier §17.2, R1, R16).

Sync par contrat : ``mcp_pappers._is_degraded()`` (S02 review C4)
appelle ``degraded()`` à chaque ``call_tool``. Une transition async
ici demanderait une refonte du hot-path Pappers — pas justifié pour
un read d'``int``.
"""

from __future__ import annotations

from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
from genial_agent.observability.stats import pappers_calls_today


def degraded() -> bool:
    """``True`` si on a atteint ou dépassé le cap journalier Pappers.

    Consommé par ``genial_agent.mcp_pappers._is_degraded()`` (résolu
    paresseusement via import optionnel — cf. S02 review C4). Dès que
    ce module est importable, S02 le branche.
    """
    return pappers_calls_today() >= DAILY_PAPPERS_CREDITS_CAP


def remaining() -> int:
    """Nombre d'appels Pappers encore autorisés aujourd'hui (≥ 0)."""
    return max(0, DAILY_PAPPERS_CREDITS_CAP - pappers_calls_today())
