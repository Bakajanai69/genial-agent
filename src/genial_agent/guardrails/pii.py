"""Scrubbing PII pour les logs applicatifs (cahier §14.4).

4 patterns FR couverts : email, téléphone fixe/mobile, IBAN FR, NIR
(sécurité sociale française).

**Scope explicite** : ce module couvre uniquement les **logs
applicatifs**. Les réponses utilisateur Pappers ne sont **pas** scrubbées
— Pappers renvoie des PII publiques (noms de dirigeants, adresses de
sièges) qui doivent rester intactes pour l'utilisateur.

S07 branchera ``pii_scrub_processor`` dans la chaîne structlog au boot
de l'app. S05 fournit le processor + ``scrub(text)`` pour usage direct
(tests, logs ad-hoc).

Sources regex :

- Phone FR : format standard fixe + mobile, séparateurs ``. - `` ou
  aucun. ``\\b0[1-9](?:[\\s.-]?\\d{2}){4}\\b``.
- IBAN FR : 27 caractères, ``FR`` + 2 chiffres + 5 groupes de 4 + 3.
- NIR FR : format INSEE (sexe + année + mois + dép + commune + ordre +
  clef). Le département peut être ``2A``/``2B`` (Corse).
"""

from __future__ import annotations

import re
from typing import Any

REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    # Email — RFC-lite suffisant pour logs
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    # Téléphone FR — fixe et mobile, séparateurs ``.`` ``-`` `` `` ou aucun
    (re.compile(r"\b0[1-9](?:[\s.-]?\d{2}){4}\b"), "[PHONE_FR]"),
    # IBAN FR — 27 chars "FRXX YYYY YYYY YYYY YYYY YYYY YYY"
    (re.compile(r"\bFR\d{2}\s?(?:\d{4}\s?){5}\d{3}\b"), "[IBAN_FR]"),
    # NIR (sécurité sociale FR) — format INSEE (2A/2B possible pour la Corse)
    (
        re.compile(r"\b[12]\s?\d{2}\s?\d{2}\s?(?:2[AB]|\d{2})\s?\d{3}\s?\d{3}\s?\d{2}\b"),
        "[NIR_FR]",
    ),
]


def scrub(text: str) -> str:
    """Remplace toute occurrence PII par un placeholder. Idempotent.

    L'ordre d'application matters : emails avant téléphones (un email
    contient des ``.`` qui pourraient être confondus). Les placeholders
    ``[EMAIL]``, ``[PHONE_FR]``, etc. ne re-matchent aucun pattern →
    idempotence par construction.
    """
    result = text
    for pattern, placeholder in REPLACEMENTS:
        result = pattern.sub(placeholder, result)
    return result


# ---------------------------------------------------------------------------
# Structlog processor — branché côté S07
# ---------------------------------------------------------------------------


def pii_scrub_processor(
    logger: Any,  # noqa: ARG001 — signature structlog imposée
    name: str,  # noqa: ARG001
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Processor ``structlog`` : scrub tous les champs ``str`` du event_dict.

    À ajouter à la chaîne de processors (S07) **avant** ``JSONRenderer``
    pour garantir qu'aucun log émis ne contient de PII.

    Usage (S07) ::

        structlog.configure(
            processors=[
                structlog.processors.TimeStamper(fmt="iso"),
                pii_scrub_processor,
                structlog.processors.JSONRenderer(),
            ],
        )

    Signature respecte le contrat structlog processor :
    ``(logger, method_name, event_dict) -> event_dict``.
    """
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = scrub(value)
    return event_dict
