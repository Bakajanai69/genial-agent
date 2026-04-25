"""Post-traitement de la réponse finale avant affichage UI.

Deux helpers :

- ``linkify_sirens`` : transforme tout SIREN 9-chiffres en lien Markdown
  vers ``pappers.fr/entreprise/{siren}``. Idempotent dans le sens UX :
  un SIREN déjà entouré d'un lien Markdown garde une forme exploitable
  (cf. test ``test_siren_already_linked_idempotent``).
- ``model_badge`` : compose le badge final du message agent à partir du
  payload de l'event ``routing_done`` (S04).
"""

from __future__ import annotations

import re

# 9 chiffres entourés de bordures de mots — cohérent ``guardrails/sirens.py``.
# ``\b`` côté Python regex ne match pas entre 2 digits consécutifs : un
# nombre de 12 chiffres ne sera pas découpé en SIREN partiel. Validé par
# ``test_siren_inside_longer_number_not_matched``.
SIREN_RE = re.compile(r"\b(\d{9})\b")


def linkify_sirens(text: str) -> str:
    """Remplace chaque SIREN 9-chiffres par un lien Markdown vers
    ``pappers.fr/entreprise/{siren}``.

    **Pas de validation Luhn** : on linkifie toute séquence 9-chiffres
    avec frontière de mot. Trade-off :

    - Faux positif possible sur un nombre 9-chiffres non-SIREN (rare en
      contexte agent FR : on parle d'entreprises). Le clic ouvre une
      page Pappers vide → pas dangereux.
    - L'output validator C5 (S05) filtre déjà les orphans Luhn-valides
      pour le disclaimer. Linkifier non-Luhn est un nice-to-have UX.

    Si on veut Luhn-only : ``from genial_agent.guardrails.sirens import
    valid_siren`` et filtrer dans le ``sub`` callback.
    """
    return SIREN_RE.sub(r"[\1](https://www.pappers.fr/entreprise/\1)", text)


def model_badge(
    *,
    model_used: str,
    escalated: bool,
    escalation_mode: str | None,
) -> str:
    """Calcule le badge modèle final à afficher en pied de message.

    Args:
        model_used: ``"haiku"`` ou ``"sonnet"`` — depuis ``routing_done``.
        escalated: ``True`` si une escalade a eu lieu pendant le turn.
        escalation_mode: ``"self"`` (Haiku a appelé ``escalate_to_sonnet``)
            ou ``"forced"`` (cap déclenché côté code).

    Returns:
        Markdown court : ``⚡ Haiku`` / ``🧠 Sonnet`` / ``⚡→🧠 Sonnet (...)``.
    """
    if escalated:
        suffix = " (auto-déclenché)" if escalation_mode == "self" else " (cap déclenché)"
        return f"⚡→🧠 Sonnet{suffix}"
    if model_used == "sonnet":
        return "🧠 Sonnet"
    return "⚡ Haiku"
