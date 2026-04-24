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
  processor PII scrub, **injection du `request_id` Anthropic** via
  contextvar.
- `src/genial_agent/observability/idempotence.py` : cache LRU TTL pour
  déduplication par `(session_id, sha256(message))`.
- `src/genial_agent/observability/stats.py` : compteurs cumulatifs
  (tokens, appels MCP, coût) **+ appels MCP du jour** pour
  l'enforcement du cap journalier (cahier §17.2).
- `src/genial_agent/observability/credit_guard.py` : mode dégradé
  cache-only quand `DAILY_PAPPERS_CREDITS_CAP` est atteint.
- **Instrumentation des call-sites** :
  - `src/genial_agent/mcp_pappers.py` : `stats.incr(total_tool_calls=1,
    pappers_calls_today=1)` avant `call_tool`, check `credit_guard`.
  - `src/genial_agent/agent.py` / `routing.py` : `stats.incr(
    total_turns=1, anthropic_input_tokens=X, anthropic_output_tokens=Y)`
    après chaque appel LLM.
  - Capture du `request_id` depuis les headers Anthropic et bind dans
    `structlog.contextvars`.
- **Intégration dans `src/genial_agent/app.py`** (modif) :
  - Check idempotence `(session_id, sha256(message))` avant de lancer
    `run_routed_turn` ; si hit dans les 60 s, renvoyer la réponse
    cached.
  - Montage des routes `/health` et `/stats` via le hook Starlette
    Chainlit.
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

### Fichiers à créer / modifier

- `src/genial_agent/observability/__init__.py`
- `src/genial_agent/observability/logging.py`
- `src/genial_agent/observability/idempotence.py`
- `src/genial_agent/observability/stats.py`
- `src/genial_agent/observability/credit_guard.py` (mode dégradé).
- `src/genial_agent/observability/routes.py` (ajoute `/health`, `/stats`).
- **Modif `src/genial_agent/app.py`** : init logging + montage routes
  + check idempotence + bandeau crédits bas (cahier §16.3).
- **Modif `src/genial_agent/mcp_pappers.py`** : `stats.incr(pappers_...
  )` et check `credit_guard.degraded()` avant chaque `call_tool`.
- **Modif `src/genial_agent/agent.py`** : capturer `request_id` +
  tokens depuis la réponse Anthropic, `stats.incr(...)`, bind
  `structlog.contextvars.bind_contextvars(request_id=...)`.

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
    pappers_calls_today_day: str = field(default_factory=lambda: datetime.utcnow().strftime("%Y-%m-%d"))
    anthropic_input_tokens: int = 0
    anthropic_output_tokens: int = 0
    errors: int = 0


_stats = Stats()
_lock = asyncio.Lock()


async def _rollover_day_if_needed() -> None:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if _stats.pappers_calls_today_day != today:
        _stats.pappers_calls_today = 0
        _stats.pappers_calls_today_day = today


async def incr(**kwargs: int) -> None:
    async with _lock:
        await _rollover_day_if_needed()
        for k, v in kwargs.items():
            setattr(_stats, k, getattr(_stats, k) + v)


async def snapshot() -> dict[str, int | str]:
    async with _lock:
        await _rollover_day_if_needed()
        return {
            "started_at": _stats.started_at,
            "total_turns": _stats.total_turns,
            "total_tool_calls": _stats.total_tool_calls,
            "pappers_calls_today": _stats.pappers_calls_today,
            "pappers_calls_today_day": _stats.pappers_calls_today_day,
            "anthropic_input_tokens": _stats.anthropic_input_tokens,
            "anthropic_output_tokens": _stats.anthropic_output_tokens,
            "errors": _stats.errors,
        }


async def pappers_calls_today() -> int:
    async with _lock:
        await _rollover_day_if_needed()
        return _stats.pappers_calls_today
```

### `credit_guard.py`

```python
"""Mode dégradé cache-only quand le cap crédits Pappers journalier
est atteint (cahier §17.2, R1, R16)."""
from __future__ import annotations

from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
from genial_agent.observability.stats import pappers_calls_today


async def degraded() -> bool:
    """True si on doit servir uniquement depuis le cache MCP (S02)."""
    return await pappers_calls_today() >= DAILY_PAPPERS_CREDITS_CAP


async def remaining() -> int:
    return max(0, DAILY_PAPPERS_CREDITS_CAP - await pappers_calls_today())
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

Points précis à ajouter dans `on_message` :

```python
from genial_agent.observability import idempotence, credit_guard
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP

session_id = cl.user_session.get("id") or "anonymous"

# 1. Idempotence — avant tout appel LLM / MCP
cached = await idempotence.cache.get(session_id, message.content)
if cached is not None:
    await cl.Message(content=cached + "\n\n_(réponse idempotence cache)_", author="Agent").send()
    return

# 2. Bandeau mode dégradé si crédits bas (<10 % restants)
remaining = await credit_guard.remaining()
if remaining < 0.1 * DAILY_PAPPERS_CREDITS_CAP:
    await cl.Message(
        content=f"⚠ Budget Pappers dégradé — reste {remaining} appels. Mode cache-only sur les entités connues.",
        author="Système",
    ).send()

# ... pipeline normal ...

# 3. Après la réponse — stocker pour idempotence
await idempotence.cache.set(session_id, message.content, msg.content)
```

### Modif `agent.py` (capture request_id + tokens)

```python
import structlog.contextvars

# Dans run_turn, après chaque appel Anthropic :
request_id = response.id  # ou extraire du header selon SDK 2026
structlog.contextvars.bind_contextvars(
    request_id=request_id,
    session_id=state.session_id,
)
await stats.incr(
    total_turns=1,
    anthropic_input_tokens=response.usage.input_tokens,
    anthropic_output_tokens=response.usage.output_tokens,
)
yield {
    "type": "llm_meta",
    "request_id": request_id,
    "input_tokens": response.usage.input_tokens,
    "output_tokens": response.usage.output_tokens,
}
```

### Modif `mcp_pappers.call_tool`

```python
# Avant chaque appel réseau :
if await credit_guard.degraded():
    cached = await mcp_cache.cache.get(name, args)
    if cached is not None:
        return cached
    raise CreditsExhausted(f"daily cap reached, no cache for {name}")

await stats.incr(total_tool_calls=1, pappers_calls_today=1)
# ... appel réseau + retry tenacity ...
```

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


async def test_credit_guard_not_degraded_initially():
    from genial_agent.observability import credit_guard
    assert await credit_guard.degraded() is False


async def test_credit_guard_degraded_after_cap(monkeypatch):
    from genial_agent.observability import credit_guard, stats as s
    from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
    # simule cap atteint
    monkeypatch.setattr(s, "_stats", type(s._stats)(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP))
    assert await credit_guard.degraded() is True
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
- [ ] `/stats` retourne un JSON avec les compteurs **non nuls après un
      premier tour** (preuve que les call-sites instrumentent).
- [ ] `stats.incr(pappers_calls_today=1)` est bien appelé dans
      `mcp_pappers.call_tool`.
- [ ] `stats.incr(total_turns=1, anthropic_..._tokens=...)` est appelé
      dans `agent.run_turn`.
- [ ] `structlog.contextvars` bind `request_id` et apparaît dans les
      logs JSON de la turn.
- [ ] `credit_guard.degraded()` déclenche bien le mode cache-only dans
      `call_tool`.
- [ ] Bandeau UI "crédits bas" apparaît quand `remaining < 10 %`.
- [ ] Rollover jour : `pappers_calls_today` revient à 0 quand la date
      change.
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
- [ ] `curl http://localhost:8000/stats` → 200 + JSON compteurs
      **non vides** après un turn réel (fiche LVMH).
- [ ] Logs en JSON valide ligne par ligne avec `request_id` quand un
      appel Anthropic a eu lieu.
- [ ] Idempotence : 2 appels identiques rapprochés ne créent qu'un seul
      appel MCP (à valider en S06+S07 intégré).
- [ ] Mode dégradé : en forçant `DAILY_PAPPERS_CREDITS_CAP=1` et en
      faisant 2 requêtes, la 2e tombe en cache-only ou lève
      `CreditsExhausted` proprement côté UI.
- [ ] `gitleaks` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S07 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
