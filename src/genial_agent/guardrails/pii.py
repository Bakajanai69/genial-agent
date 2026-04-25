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


def _scrub_value(value: Any) -> Any:
    """Scrub récursif : str scrubbé directement, dict / list / tuple
    descendus, autres types retournés tels quels.

    Couvre les valeurs imbriquées (``logger.info("evt", details={"email":
    "x@y.z"})``) **et** les structures produites par
    ``dict_tracebacks`` (liste de dicts représentant les frames d'une
    exception, qui peuvent transporter des PII via
    ``locals`` / message d'exception).

    Résolu post-review S07 (B2) : la version précédente ne scrubbait
    que les ``str`` de premier niveau, laissant fuiter tout PII niché.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    return value


# ---------------------------------------------------------------------------
# Structlog processor — branché côté S07
# ---------------------------------------------------------------------------


def pii_scrub_processor(
    logger: Any,  # noqa: ARG001 — signature structlog imposée
    name: str,  # noqa: ARG001
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """Processor ``structlog`` : scrub récursivement tous les ``str`` du
    event_dict, y compris ceux nichés dans des ``dict`` / ``list`` /
    ``tuple``.

    À placer dans la chaîne **après** ``dict_tracebacks`` (qui
    transforme ``exc_info`` en liste de dicts avec frames + locals)
    pour que le scrub voie aussi les contenus d'exception.

    Signature respecte le contrat structlog processor :
    ``(logger, method_name, event_dict) -> event_dict``.
    """
    for key, value in list(event_dict.items()):
        event_dict[key] = _scrub_value(value)
    return event_dict
