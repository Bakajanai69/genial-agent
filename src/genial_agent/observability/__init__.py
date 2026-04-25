"""Observability S07 — structlog JSON, idempotence, stats, /health, /stats."""

from __future__ import annotations

from genial_agent.observability.credit_guard import degraded, remaining
from genial_agent.observability.idempotence import IdempotenceCache, cache
from genial_agent.observability.logging import configure_logging
from genial_agent.observability.mount import mount_routes
from genial_agent.observability.stats import (
    incr,
    pappers_calls_today,
    snapshot,
)

__all__ = [
    "IdempotenceCache",
    "cache",
    "configure_logging",
    "degraded",
    "incr",
    "mount_routes",
    "pappers_calls_today",
    "remaining",
    "snapshot",
]
