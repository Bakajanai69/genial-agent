# S07 — Observabilité + healthcheck + idempotence

> **Statut** : ⬜ à faire
> **Durée estimée** : 1 h
> **Parallélisable avec** : S08

---

## 📍 Contexte

Structured logging JSON, idempotence applicative, endpoints HTTP
`/health` et `/stats`, PII scrubbing dans les logs. Ces briques
conditionnent la crédibilité enterprise (cf. Cegid / Crédit Agricole).

Sources de vérité :
- `docs/cahier-des-charges.md` §14.4 (observabilité), §17 (opérations).
- Stories S02 (healthcheck MCP), S05 (PII scrub existe déjà).

---

## 🔒 Prérequis

- [ ] S01, S02, S03 terminées.
- [ ] S05 idéalement (PII scrubber réutilisé).

## 🔑 Inputs utilisateur requis

- Aucun nouveau. Aucun token secret n'est requis pour `/stats`, c'est un
  endpoint interne consultable uniquement via Railway logs. Si on veut
  le rendre accessible publiquement, ajouter un `STATS_TOKEN` à `.env`.

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/observability/logging.py` : config `structlog` JSON,
  processor PII scrub.
- `src/genial_agent/observability/idempotence.py` : cache LRU TTL pour
  déduplication par `(session_id, sha256(message))`.
- `src/genial_agent/observability/stats.py` : compteurs cumulatifs
  (tokens, appels MCP, coût).
- Endpoint `/health` et `/stats` ajoutés en parallèle de Chainlit (via
  route Starlette / FastAPI).

### Hors scope

- Déploiement Railway et UptimeRobot (S08).
- Tracing distribué Langfuse (documenté next step).

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Comment exposer des routes HTTP supplémentaires sous Chainlit en
      2026. Chainlit tourne sur Starlette : trouver le hook officiel
      pour ajouter une route (`@cl.on_start_app`, ou accès au `app`
      Starlette sous-jacent).
- [ ] Config `structlog` JSON avec tous les processors standards
      (timestamp ISO, request_id contextvar, PII scrubber custom,
      `EventRenamer`, `JSONRenderer`).
- [ ] Pattern idempotence : `functools.lru_cache` ne gère pas TTL →
      utiliser `cachetools.TTLCache` ou implémentation maison.
- [ ] Vérifier le format JSON attendu par Railway pour le parsing des
      logs (les champs standards à exposer).

### Points à résoudre

- [ ] Idempotence : où l'appliquer ? Reco → au niveau
      `on_message` Chainlit : avant de lancer `run_routed_turn`, check
      cache `(session_id, hash(msg))`. Si hit récent, renvoyer la
      même réponse cached sans re-consommer.
- [ ] Stats : où stocker ? In-memory dict thread-safe (asyncio.Lock)
      suffit pour l'exo. Pas de Redis.

### Commit phase 1

`story(S07): refine — Chainlit Starlette hook, structlog processors, TTLCache`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer

- `src/genial_agent/observability/__init__.py`
- `src/genial_agent/observability/logging.py`
- `src/genial_agent/observability/idempotence.py`
- `src/genial_agent/observability/stats.py`
- `src/genial_agent/observability/routes.py` (ajoute `/health`, `/stats`).
- Modif `src/genial_agent/app.py` pour initialiser logging + routes.

### `logging.py`

```python
"""Configuration structlog JSON avec PII scrub."""
from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from genial_agent.config import settings
from genial_agent.guardrails.pii import scrub


def _pii_processor(logger, method_name, event_dict):  # noqa: ANN001, ARG001
    for k, v in list(event_dict.items()):
        if isinstance(v, str):
            event_dict[k] = scrub(v)
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _pii_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL, logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
```

### `idempotence.py`

```python
"""Cache applicatif (session_id, message_hash) → réponse."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from time import monotonic

DEFAULT_TTL_S = 60


@dataclass
class _Entry:
    value: str
    expires_at: float


class IdempotenceCache:
    def __init__(self, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._ttl = ttl_s
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(session_id: str, message: str) -> str:
        h = hashlib.sha256(message.encode()).hexdigest()[:16]
        return f"{session_id}:{h}"

    async def get(self, session_id: str, message: str) -> str | None:
        k = self.key(session_id, message)
        async with self._lock:
            entry = self._store.get(k)
            if not entry:
                return None
            if entry.expires_at < monotonic():
                del self._store[k]
                return None
            return entry.value

    async def set(self, session_id: str, message: str, value: str) -> None:
        k = self.key(session_id, message)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=monotonic() + self._ttl)


cache = IdempotenceCache()
```

### `stats.py`

```python
"""Compteurs globaux pour l'endpoint /stats."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Stats:
    started_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    total_turns: int = 0
    total_tool_calls: int = 0
    pappers_calls_today: int = 0
    anthropic_input_tokens: int = 0
    anthropic_output_tokens: int = 0
    errors: int = 0


_stats = Stats()
_lock = asyncio.Lock()


async def incr(**kwargs: int) -> None:
    async with _lock:
        for k, v in kwargs.items():
            setattr(_stats, k, getattr(_stats, k) + v)


async def snapshot() -> dict[str, int | str]:
    async with _lock:
        return {
            "started_at": _stats.started_at,
            "total_turns": _stats.total_turns,
            "total_tool_calls": _stats.total_tool_calls,
            "pappers_calls_today": _stats.pappers_calls_today,
            "anthropic_input_tokens": _stats.anthropic_input_tokens,
            "anthropic_output_tokens": _stats.anthropic_output_tokens,
            "errors": _stats.errors,
        }
```

### `routes.py`

```python
"""Routes HTTP /health et /stats, montées sur l'app Starlette Chainlit."""
from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse

from genial_agent import mcp_pappers
from genial_agent.observability.stats import snapshot


async def health(request: Request) -> JSONResponse:  # noqa: ARG001
    result = await mcp_pappers.healthcheck()
    return JSONResponse({"status": result["status"], "mcp": result})


async def stats(request: Request) -> JSONResponse:  # noqa: ARG001
    return JSONResponse(await snapshot())
```

### Modif `app.py`

Ajouter l'enregistrement des routes au démarrage — pattern Chainlit 2026
à confirmer en phase 1 (via `cl.app` ou `fastapi_app()`).

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S07_observability.py
import asyncio
import pytest

from genial_agent.observability.idempotence import IdempotenceCache
from genial_agent.observability.stats import incr, snapshot


async def test_idempotence_hit():
    cache = IdempotenceCache(ttl_s=10)
    await cache.set("sess1", "hello", "response A")
    assert await cache.get("sess1", "hello") == "response A"


async def test_idempotence_miss_different_session():
    cache = IdempotenceCache(ttl_s=10)
    await cache.set("sess1", "hello", "response A")
    assert await cache.get("sess2", "hello") is None


async def test_idempotence_expires():
    cache = IdempotenceCache(ttl_s=0)  # immediate expiry
    await cache.set("sess1", "hello", "A")
    await asyncio.sleep(0.01)
    assert await cache.get("sess1", "hello") is None


async def test_stats_increment_and_snapshot():
    before = await snapshot()
    await incr(total_turns=1, total_tool_calls=3)
    after = await snapshot()
    assert after["total_turns"] == before["total_turns"] + 1
    assert after["total_tool_calls"] == before["total_tool_calls"] + 3
```

#### Logging

```python
# tests/unit/test_S07_logging.py
import json
import structlog

from genial_agent.observability.logging import configure_logging


def test_pii_scrubbed_in_log_output(capsys):
    configure_logging()
    logger = structlog.get_logger()
    logger.info("test_event", email="leak@example.com", phone="06 12 34 56 78")
    captured = capsys.readouterr().out
    line = json.loads(captured.strip().split("\n")[-1])
    assert line["email"] == "[EMAIL]"
    assert line["phone"] == "[PHONE_FR]"
```

### Commit phase 2

`feat(S07): structlog JSON + idempotence cache + /health + /stats`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] `/health` retourne `{"status": "ok", ...}` en local (curl test).
- [ ] `/stats` retourne un JSON avec les compteurs.
- [ ] Tous les logs applicatifs passent par `structlog`, pas de `print`
      résiduel.
- [ ] Le PII processor scrub bien les champs string (test unitaire vert).
- [ ] `IdempotenceCache` thread-safe (asyncio.Lock utilisé).
- [ ] Aucun secret ou URL complète de Pappers n'apparaît dans un log,
      même en DEBUG.

### Commit phase 3

`review(S07): approved`

---

## ✅ Critères d'acceptation

- [ ] `curl http://localhost:8000/health` → 200 + JSON avec status.
- [ ] `curl http://localhost:8000/stats` → 200 + JSON compteurs.
- [ ] Logs en JSON valide ligne par ligne.
- [ ] Idempotence : 2 appels identiques rapprochés ne créent qu'un seul
      appel MCP (à valider en S06+S07 intégré).
- [ ] `gitleaks` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S07 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
