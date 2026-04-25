"""Configuration ``structlog`` JSON avec PII scrub + contextvars merge.

Branche le ``pii_scrub_processor`` de ``guardrails.pii`` (S05) ; aucune
duplication de regex. La chaîne ci-dessous est figée 2026-04-25
(cf. S07 phase 1 §"Chaîne structlog 25.5 retenue").

Idempotente : appelable plusieurs fois sans effet — ``app.py`` la pose
au module-load avant que Chainlit serve une requête, et chaque test
unitaire qui assert sur les logs peut la rappeler après
``reset_for_tests`` pour repartir d'un état propre.
"""

from __future__ import annotations

import logging
import sys

import structlog

from genial_agent.config import settings
from genial_agent.guardrails.pii import pii_scrub_processor

_CONFIGURED = False


def _resolve_level() -> int:
    return getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)


def configure_logging() -> None:
    """Configure ``structlog`` en JSON. Idempotent.

    L'ordre des processors est figé :

    1. ``merge_contextvars`` — injecte ``session_id`` / ``request_id``
       avant les autres processors pour qu'un PII bind-é par erreur soit
       scrubbé en aval.
    2. ``add_log_level`` — ajoute le champ ``level``.
    3. ``TimeStamper(fmt="iso", utc=True)`` — horodatage ISO 8601 UTC.
    4. ``dict_tracebacks`` — exceptions structurées en JSON pour Railway.
       **Placé avant** ``pii_scrub_processor`` (post-review B2) pour que
       le scrub voie aussi les frames / locals de la stacktrace.
    5. ``pii_scrub_processor`` — S05 (email / téléphone FR / IBAN / NIR).
       Récursif sur dict / list / tuple depuis review B2.
    6. ``EventRenamer(to="msg")`` — Railway / Datadog cherchent ``msg``.
    7. ``JSONRenderer`` — sortie ligne par ligne JSON valide.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    level = _resolve_level()

    logging.basicConfig(
        stream=sys.stdout,
        level=level,
        format="%(message)s",
        force=True,
    )

    # ``httpx`` émet à INFO « HTTP Request: POST <full url> "..." » — l'URL
    # Pappers contient la clé API dans le path (cf. ``docs/pappers-mcp.md``
    # §2 + cahier R2). Notre ``basicConfig(level=INFO)`` ci-dessus active
    # par ricochet ce logger. On le **muselle au-dessus de WARNING**
    # uniquement pour ce module — la traçabilité réseau passe par
    # ``mcp_pappers`` (latency_ms, tool_name, jamais l'URL).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Idem ``httpcore`` (couche transport) qui peut logguer des hostnames
    # ou des targets en DEBUG/INFO selon la version.
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.dict_tracebacks,
            pii_scrub_processor,
            structlog.processors.EventRenamer(to="msg"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def reset_for_tests() -> None:
    """Force un reconfigure au prochain ``configure_logging``. Tests only.

    Utile quand un test veut basculer ``LOG_LEVEL`` ou vérifier la
    re-configurabilité. ``structlog.reset_defaults()`` purge le cache
    interne du wrapper.
    """
    global _CONFIGURED
    structlog.reset_defaults()
    _CONFIGURED = False
