# S07 — Observabilité + healthcheck + idempotence

> **Statut** : 🟢 raffinée (phase 1 terminée 2026-04-25) — phase 2 prête
> **Durée estimée** : 1 h 30 (était 1 h — ré-évaluée à la hausse :
> instrumenter 4 call-sites + override de la route Chainlit `/health` +
> sortir un test pytest+httpx du serveur Chainlit n'est pas trivial)
> **Parallélisable avec** : S08

---

## 📍 Contexte

Structured logging JSON, idempotence applicative, endpoints HTTP
`/health` et `/stats`, PII scrubbing dans les logs. Ces briques
conditionnent la crédibilité enterprise (cf. Cegid / Crédit Agricole
dans le cahier).

**Le module ``guardrails/pii.py`` (S05) expose déjà ``scrub`` et
``pii_scrub_processor`` — S07 ne ré-implémente rien**, il branche le
processor dans la chaîne ``structlog`` au boot.

Sources de vérité :

- `docs/cahier-des-charges.md` §14.4 (observabilité), §16.3 (bandeau
  crédits bas), §17 (opérations), §17.2 (cap journalier 100).
- Story S02 (`mcp_pappers.healthcheck` contrat 4 clés, hook
  `_is_degraded` paresseux + mémoïsé), S03 (event ``llm_meta``
  contient `request_id`, `input_tokens`, `output_tokens`,
  `latency_ms`, `stop_reason`), S04 (events `routing_done` /
  `escalation` / `capped`), S05 (event `capped` token budget +
  pipeline `run_guarded_turn`), S06 (`app.py` entry point Chainlit).
- README §"Décisions de cohérence" §3 (call-sites stats), §4 (mode
  dégradé `credit_guard.degraded()` lit `DAILY_PAPPERS_CREDITS_CAP`
  depuis `caps.py` S05), §5 (un seul `@cl.on_chat_start` — S07 n'en
  ajoute pas).

---

## 🔒 Prérequis

- [x] S01, S02, S03 terminées.
- [x] S05 mergée (`guardrails/pii.py` exporte `pii_scrub_processor`).
- [x] S06 mergée (`app.py` Chainlit existe — S07 le modifie).

## 🔑 Inputs utilisateur requis

- [x] Aucun nouveau secret obligatoire.
- [ ] **Optionnel** `STATS_TOKEN` dans `.env` — si défini, l'endpoint
      `/stats` exige `Authorization: Bearer <token>` ; sinon il est
      **ouvert** sur le déploiement public Railway. Décision MVP :
      laisser ouvert (la surface d'attaque est minime — compteurs
      cumulés sans PII), documenter la mitigation dans le README. Le
      review S07 pousse l'utilisateur à coller un token avant publier.

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/observability/__init__.py` — ré-exports publics.
- `src/genial_agent/observability/logging.py` — `configure_logging()`
  pose la chaîne structlog JSON avec PII scrub + contextvars merge +
  EventRenamer (Railway lit `msg` mieux que `event`).
- `src/genial_agent/observability/idempotence.py` —
  `IdempotenceCache` TTL 60 s `(session_id, sha256(message))`. Pattern
  `mcp_cache.ToolCache` (S02) : asyncio.Lock + monotonic deadline +
  cleanup paresseux. Singleton `cache` exporté.
- `src/genial_agent/observability/stats.py` — dataclass `Stats` +
  helpers **synchrones** `incr(**kwargs)`, `snapshot()`,
  `pappers_calls_today()`. Rollover jour UTC.
- `src/genial_agent/observability/credit_guard.py` — `degraded()` /
  `remaining()` **synchrones**. Lecture de
  `DAILY_PAPPERS_CREDITS_CAP` depuis `guardrails/caps.py`.
- `src/genial_agent/observability/routes.py` — handlers FastAPI
  `health(request)` et `stats(request)`. Le module **n'instancie pas**
  de FastAPI ni de router — ses fonctions sont prepend-ées sur
  `chainlit.server.app.router.routes` par `mount_routes()`.
- **Instrumentation des call-sites** :
  - `src/genial_agent/mcp_pappers.py:461` (`logger.info("pappers_call_ok"…)`)
    → ajouter `stats.incr(total_tool_calls=1, pappers_calls_today=1)`
    juste après `cache.set` (ne pas compter les hits cache, **ne pas
    compter les errors business**).
  - `src/genial_agent/agent.py:283-293` (l'event `llm_meta`) — pas
    d'instrumentation locale ; les compteurs sont alimentés par le
    pipeline qui voit l'event (voir ligne suivante). Côté `agent.py`
    on **ajoute** seulement le bind contextvar du `request_id` une
    fois capturé, pour que les logs internes au scope du turn portent
    l'ID.
  - `src/genial_agent/guardrails/pipeline.py:146-149` (`elif etype ==
    "llm_meta":`) — **ajouter** `stats.incr(total_turns=1,
    anthropic_input_tokens=in_tok, anthropic_output_tokens=out_tok)`
    après `await budget.add(...)`. Le pipeline est **le seul endroit**
    qui voit tous les `llm_meta` (Haiku initial + Sonnet escalade) —
    instrumenter dans `agent.run_turn` doublerait les compteurs sur
    une escalade.
  - `src/genial_agent/app.py:140` (`@cl.on_message`) — wrapper le corps
    dans `with structlog.contextvars.bound_contextvars(session_id=
    session_id):` avant `dispatch_event`. Bind l'idempotence avant
    `run_guarded_turn`.
- **Modif `src/genial_agent/app.py`** — appel à
  `observability.configure_logging()` au top-level + bandeau crédits
  bas dans `on_message` + check idempotence avant `run_guarded_turn`.
- **Modif `src/genial_agent/mcp_pappers.py`** — `stats.incr` au call-site
  succès (`call_tool` ligne 461). Le bypass `_is_degraded` est déjà
  branché (lignes 384-394, S02 review C4) — S07 fournit juste le
  module qui le résout.
- `src/genial_agent/observability/mount.py` — fonction `mount_routes()`
  qui prepend `/health` (override Chainlit) et `/stats` sur
  `chainlit.server.app.router.routes`. Appelée depuis `app.py` au
  module-load (avant que Chainlit serve la 1ère requête).

### Hors scope

- Déploiement Railway et UptimeRobot (S08).
- Tracing distribué Langfuse / OpenTelemetry (`next step` README S09).
- Endpoint `/metrics` Prometheus — `next step` (les compteurs
  `/stats` couvrent l'usage MVP, Prometheus demande un format
  spécifique sans valeur ajoutée pour la démo).
- Audit trail append-only (mentionné cahier §14.5 next step).

---

## 🧭 Phase 1 — Elicitation Agent

### ✅ Conclusions elicitation (2026-04-25)

Recherches effectuées par :

- Inspection in-process des packages installés : `chainlit==2.11.1`,
  `structlog==25.5.0`, `anthropic==0.97.0` (.venv local).
- [Chainlit FastAPI integration docs](https://docs.chainlit.io/integrations/fastapi)
  + [chainlit/server.py](https://github.com/Chainlit/chainlit/blob/main/backend/chainlit/server.py)
  pour la mécanique mount + ordering routes.
- Source `anthropic.lib.streaming._messages.AsyncMessageStream`
  (vérification `request_id` property).

#### Versions pinnées

| Lib | Version | Source |
|---|---|---|
| `structlog` | `25.5.0` | déjà dans `pyproject.toml` (S01) |
| `chainlit` | `2.11.1` | idem |
| `anthropic` | `0.97.0` | idem |
| Python | `3.12.3` | idem |

**Aucun bump nécessaire** pour S07. Aucune dep externe ajoutée
(`cachetools` est tentant pour `TTLCache` mais ajoute 30 Ko + une dep
pour 30 lignes de code — on garde une implémentation maison cohérente
avec `mcp_cache.ToolCache` S02).

#### ⚖️ Décision majeure — endpoints HTTP via prepend route Chainlit

**Constat** (inspection `chainlit.server` 2.11.1) :

```
>>> import chainlit.server as cls
>>> [r.path for r in cls.app.router.routes[-3:]]
['/', '/health', '/{full_path:path}']
```

Chainlit 2.10+ expose déjà `/health` qui retourne `{"status": "ok"}`
**statique** (sans ping MCP). Le catch-all `/{full_path:path}` sert
l'UI React en dernier recours.

Trois options évaluées pour ajouter `/health` riche + `/stats` :

1. **Mounter Chainlit sur un FastAPI custom** — pattern documenté
   (`from chainlit.utils import mount_chainlit; mount_chainlit(app,
   target="src/genial_agent/app.py", path="")`). Demande de switcher
   le runner de `chainlit run` à `uvicorn server:app`. **Rejeté** :
   change le Makefile, le Dockerfile S08 et impose un fichier
   server.py. Friction non justifiée pour un exo week-end.
2. **Middleware ASGI qui intercepte `/health` et `/stats`** —
   robuste mais lourd à tester (mock ASGI scope) et masque le
   debugging par stack trace standard. **Rejeté**.
3. **Prepend de routes sur `chainlit.server.app.router.routes`**
   après l'import de `chainlit`. FastAPI matche dans l'ordre du tableau
   `routes` → notre `/health` shadow celui de Chainlit + notre
   `/stats` est servi avant le catch-all. **Retenu** : 6 lignes,
   pas de changement au runner ni à la CI. Valide aussi pour S08
   (Railway) — la commande `chainlit run` reste la même.

```python
# observability/mount.py (squelette en phase 2)
from chainlit.server import app as cl_app
from starlette.routing import Route
from genial_agent.observability.routes import health, stats

def mount_routes() -> None:
    cl_app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    cl_app.router.routes.insert(1, Route("/stats", stats, methods=["GET"]))
```

L'`override` de `/health` est volontaire : on veut un healthcheck qui
ping le MCP, pas le `{"status":"ok"}` statique de Chainlit. Documenté
en commentaire pour qu'un futur upgrade Chainlit (3.x) ne soit pas
surpris si l'override disparaît.

#### Chaîne ``structlog`` 25.5 retenue

```python
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,           # session_id, request_id
        structlog.processors.add_log_level,                # ajoute "level"
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        pii_scrub_processor,                               # S05, déjà existant
        structlog.processors.dict_tracebacks,              # tb structuré JSON
        structlog.processors.EventRenamer(to="msg"),       # "event" → "msg"
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(logging, settings.LOG_LEVEL, logging.INFO)
    ),
    context_class=dict,
    cache_logger_on_first_use=True,
)
```

Choix justifiés :

- **`merge_contextvars` en premier** : pour qu'un appel à
  `bind_contextvars(session_id=...)` injecte avant les autres
  processors. Indispensable pour que `pii_scrub_processor` voie déjà
  les valeurs et puisse scrubber un email accidentellement bind-é.
- **`add_log_level` avant timestamp** : pas critique, ordre standard
  documenté.
- **`pii_scrub_processor` (S05) avant le rendu JSON** : scrub tous les
  `str` du `event_dict`, idempotent. Couvre email / téléphone FR /
  IBAN FR / NIR FR.
- **`dict_tracebacks`** : remplace `format_exc_info` ; structure les
  exceptions en JSON (Railway les indexe correctement). Présent en
  25.5 (vérifié par introspection).
- **`EventRenamer(to="msg")`** : Railway / Datadog cherchent un champ
  `msg` ou `message` ; renommer dès l'origine évite un hack côté
  parser. Présent en 25.5 (vérifié par introspection).
- **`make_filtering_bound_logger`** : remplace l'ancien
  `wrapper_class=structlog.stdlib.BoundLogger` pour ne pas avoir
  besoin de configurer `logging` en parallèle. Plus rapide.

#### Capture du `request_id` Anthropic

Le SDK 0.97 expose `stream.request_id` comme property qui retourne
`stream.response.headers.get("request-id")` (vérifié par
``inspect.getsource`` sur `AsyncMessageStream`). S03 le capture déjà
en ligne `agent.py:237` (`request_id = stream.request_id`) et le
forwarde dans `llm_meta`. **S07 n'a rien à modifier dans `agent.py`
côté capture** ; il **ajoute** :

- côté `app.py` `on_message` : un `bound_contextvars(session_id=…)`
  qui scope toute la conversation.
- côté `pipeline.py` `llm_meta` handler : un `bind_contextvars(
  request_id=event["request_id"])` à chaque appel Claude — chaque
  binding écrase le précédent (request_id est par-appel, pas
  par-turn), c'est attendu.

`structlog.contextvars` repose sur `contextvars.ContextVar` qui est
**task-local** en asyncio (chaque task a sa propre copy-on-write
context). Deux conversations en parallèle n'écrasent pas mutuellement
leurs binds — testé via le test unitaire
`test_contextvars_isolation_between_concurrent_turns` (cf. tests).

#### Stats : sync vs async

Le squelette initial proposait `async def incr(...)` avec
`asyncio.Lock`. **Ré-évaluation** :

- Asyncio est cooperative ; un `setattr(stats, k, v + n)` ne suspend
  pas — pas de race condition entre tasks sur un compteur simple.
- L'`asyncio.Lock` n'apporte de garantie que sur des opérations
  composées (read-modify-write multi-champ). `incr(a=1, b=2)` aurait
  besoin du lock SI un autre task pouvait observer un état
  intermédiaire. Or `snapshot()` est l'unique reader public, et on
  documente qu'il est best-effort (consistency éventuelle suffisante
  pour `/stats` en démo).
- `mcp_pappers._is_degraded()` est **sync** (déjà mergé S02 review C4)
  → si `degraded()` était async, on devrait modifier S02. **On garde
  sync** pour rester cohérent avec le contrat existant.

Décision : `stats.incr`, `stats.snapshot`, `stats.pappers_calls_today`,
`credit_guard.degraded`, `credit_guard.remaining` sont **toutes
synchrones**. Une seule contention théorique : le rollover jour, géré
par un guard idempotent (cf. code phase 2).

#### Idempotence : portée et durée

- **Portée** : `(session_id, sha256(message))`. Deux sessions
  différentes peuvent envoyer le même texte sans conflit.
- **TTL** : 60 s. Couvre double-submit, refresh navigateur, retry
  utilisateur, mais expire avant qu'une vraie 2ᵉ question identique
  arrive (un évaluateur qui repose la même fiche LVMH 2 min plus tard
  doit re-déclencher l'agent — sinon il croit que la démo est cassée).
- **Stockage** : in-memory, pas de Redis (cahier §9 scope négatif).
- **Storage value** : `str` (le texte de la réponse agent). On
  **n'idempotente pas** les events intermédiaires (text deltas, tool
  calls) — l'utilisateur en cas de hit verra un message complet d'un
  coup avec un suffixe `_(idempotence cache)_`. Trade-off accepté :
  pas de re-streaming UX mais 0 crédit consommé.
- **Quand stocker** : à la fin de `on_message` après le pipeline,
  une fois `msg.content` final connu (post-linkify, post-badge).
- **Quand bypasser** : si `turn_state.input_rejected` (l'input a été
  refusé par C1, on ne stocke pas la réponse "Garde-fou").

#### `/stats` : endpoint semi-protégé

| Cas | Comportement |
|---|---|
| `STATS_TOKEN` non défini | Endpoint **ouvert** — convenu MVP, surface minime. |
| `STATS_TOKEN` défini, header `Authorization: Bearer <token>` correct | 200 OK. |
| `STATS_TOKEN` défini, header absent ou faux | 401 Unauthorized. |

Ajout `.env.example` : ligne commentée
`# STATS_TOKEN=  # protect /stats endpoint behind a bearer token (recommended in prod)`.

#### `/health` : contrat durci

Retourne 4 clés stables (alignement S02 review B2) :

```json
{
  "status": "ok" | "ko",
  "mcp": {"status": "ok"|"ko", "latency_ms": int, "tools_count": int, "error": str|null},
  "version": "0.1.0",
  "uptime_s": int
}
```

- `status` agrège : `"ok"` si MCP `ok`, sinon `"ko"`.
- `version` lu depuis `genial_agent.__version__` (S01 a déjà fixé ce
  contrat dans le test smoke `test_package_has_version`).
- `uptime_s` calculé par delta sur `stats._stats.started_at`.

UptimeRobot (S08) pingera cet endpoint toutes les 5 min. Le code HTTP
**reste 200** même si MCP KO — UptimeRobot ne doit pas page Lancelot
sur une indispo Pappers temporaire. La sémantique alarme est portée
par `status` dans le body, pas par le code HTTP. Documenté.

#### Idempotence et events streaming — réconciliation

Le pipeline yield des events incrémentaux (`text`, `tool_use`…). En
cas de hit cache idempotence, on **n'a pas** ces events à re-streamer.
Décision : on rend la réponse cached d'un coup avec un suffixe
explicite. C'est rare en démo (l'évaluateur ne repose pas la même
question dans les 60 s), donc l'UX dégradée est acceptable. La
bannière entité active n'est pas mise à jour sur un cache hit (pas de
`tool_result` events à scanner), ce qui est cohérent avec "les caches
préservent l'état précédent".

#### Test runner sur les routes HTTP

`fastapi.testclient.TestClient` lit `chainlit.server.app` directement.
**Mais** : `mount_routes()` n'est pas appelée tant que `app.py` n'est
pas importé par Chainlit. Solution test :

```python
# tests/unit/test_S07_routes.py
from fastapi.testclient import TestClient
from genial_agent.observability.mount import mount_routes
from chainlit.server import app as cl_app

@pytest.fixture(autouse=True)
def _mount_once():
    mount_routes()           # idempotent (cf. flag interne)
    yield
    # pas de unmount — les routes prepend-ées restent, c'est OK pour
    # les tests suivants. Idempotence vérifiée via flag _MOUNTED.

def test_health_endpoint_returns_4_keys(monkeypatch):
    async def fake_mcp_health():
        return {"status": "ok", "latency_ms": 12, "tools_count": 7, "error": None}
    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_mcp_health)
    client = TestClient(cl_app)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body.keys()) >= {"status", "mcp", "version", "uptime_s"}
```

#### Points résolus / décisions

- [x] **Hook Chainlit pour les routes HTTP** : prepend manuel sur
      `chainlit.server.app.router.routes` ; pas d'override du runner.
- [x] **structlog chain** : 7 processors fixés, ordre figé.
- [x] **PII processor** : on réutilise `guardrails.pii_scrub_processor`
      (S05), pas de duplication.
- [x] **TTLCache idempotence** : implémentation maison (asyncio.Lock,
      30 lignes) — pas de dep `cachetools`.
- [x] **Stats sync** : pas de `asyncio.Lock`, asyncio cooperative
      garantit l'atomicité d'un `setattr`. Cohérent avec
      `mcp_pappers._is_degraded()` (sync).
- [x] **Rollover jour** : `pappers_calls_today_day = utcnow().date()`
      idempotent dans chaque incr/snapshot (re-check à chaque appel,
      reset si différent).
- [x] **Override de `/health`** : volontaire ; documenté.
- [x] **`/stats` token-gated optionnel** : `STATS_TOKEN` env, défaut
      ouvert. Ajout `.env.example` ligne commentée.
- [x] **Ordre call-sites** : `mcp_pappers.py:461` (call_tool succès),
      `pipeline.py:146-149` (llm_meta handler). **Pas** dans
      `agent.py` pour éviter le double-comptage en cas d'escalade.
- [x] **Capture `request_id`** : déjà faite par S03 dans
      `agent.py:237`. S07 ne fait que le binder en contextvar.

### Commit phase 1

`story(S07): refine — Chainlit prepend routes, structlog 25.5 chain, sync stats, call-site refs`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

**Créer** :

- `src/genial_agent/observability/__init__.py`
- `src/genial_agent/observability/logging.py`
- `src/genial_agent/observability/idempotence.py`
- `src/genial_agent/observability/stats.py`
- `src/genial_agent/observability/credit_guard.py`
- `src/genial_agent/observability/routes.py`
- `src/genial_agent/observability/mount.py`
- `tests/unit/test_S07_idempotence.py`
- `tests/unit/test_S07_stats.py`
- `tests/unit/test_S07_credit_guard.py`
- `tests/unit/test_S07_logging.py`
- `tests/unit/test_S07_routes.py`

**Modifier** :

- `src/genial_agent/app.py` — call `configure_logging()` + `mount_routes()`
  au module-load ; `bound_contextvars(session_id=…)` autour du
  dispatcher ; check idempotence avant `run_guarded_turn` ; bandeau
  crédits bas avant le pipeline ; store idempotence après le pipeline.
- `src/genial_agent/mcp_pappers.py` — `stats.incr(total_tool_calls=1,
  pappers_calls_today=1)` à la ligne **après** `cache.set` (ligne 467
  actuelle : insérer juste après `await cache.set(name, args, payload)`,
  **avant** le `return payload`). N'incrémente pas sur cache hit, ni
  sur business error.
- `src/genial_agent/guardrails/pipeline.py` — `stats.incr(total_turns=1,
  anthropic_input_tokens=in_tok, anthropic_output_tokens=out_tok)` +
  `bind_contextvars(request_id=event["request_id"])` dans la branche
  `elif etype == "llm_meta"` (lignes 146-156).
- `.env.example` — ajout ligne commentée `STATS_TOKEN=`.

### `observability/logging.py`

```python
"""Configuration structlog JSON avec PII scrub + contextvars merge.

Branche le ``pii_scrub_processor`` de ``guardrails.pii`` (S05), évite
toute duplication de regex. La chaîne complète ci-dessous est figée
2026-04-25 (cf. S07 phase 1).
"""

from __future__ import annotations

import logging
import sys

import structlog

from genial_agent.config import settings
from genial_agent.guardrails.pii import pii_scrub_processor

_CONFIGURED = False


def configure_logging() -> None:
    """Configure structlog en JSON. Idempotent.

    Appelée au module-load de ``app.py`` (avant que Chainlit serve la
    1ère requête) **et** au début de chaque test unitaire qui assert
    sur les logs (les fixtures `caplog` / `capsys` réinitialisent
    structlog par défaut, on doit re-configurer).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
        format="%(message)s",
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            pii_scrub_processor,
            structlog.processors.dict_tracebacks,
            structlog.processors.EventRenamer(to="msg"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
        context_class=dict,
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def reset_for_tests() -> None:
    """Force un reconfigure au prochain ``configure_logging``. Tests
    only — utile pour tester un changement de ``LOG_LEVEL``.
    """
    global _CONFIGURED
    structlog.reset_defaults()
    _CONFIGURED = False
```

### `observability/idempotence.py`

```python
"""Cache applicatif (session_id, message_hash) → réponse texte.

Pattern aligné sur ``mcp_cache.ToolCache`` (S02) : asyncio.Lock,
deadline `monotonic`, cleanup paresseux. TTL court (60 s) pour
absorber double-submit / refresh sans bloquer une vraie 2ᵉ question
identique posée plus tard.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic

DEFAULT_TTL_S = 60
MAX_ENTRIES = 256  # Soft cap, démo ~10 sessions × 5 msgs : marge confortable


@dataclass
class _Entry:
    value: str
    expires_at: float


class IdempotenceCache:
    def __init__(self, ttl_s: int = DEFAULT_TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        self._ttl = ttl_s
        self._max = max_entries
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = asyncio.Lock()

    @staticmethod
    def key(session_id: str, message: str) -> str:
        h = hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest()[:16]
        return f"{session_id}:{h}"

    async def get(self, session_id: str, message: str) -> str | None:
        k = self.key(session_id, message)
        async with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return None
            if entry.expires_at < monotonic():
                del self._store[k]
                return None
            self._store.move_to_end(k)  # LRU sur read
            return entry.value

    async def set(self, session_id: str, message: str, value: str) -> None:
        k = self.key(session_id, message)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=monotonic() + self._ttl)
            self._store.move_to_end(k)
            while len(self._store) > self._max:
                self._store.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()


cache = IdempotenceCache()
```

### `observability/stats.py`

```python
"""Compteurs cumulatifs in-memory pour /stats et le mode dégradé.

API **synchrone** : asyncio est cooperative, ``setattr`` sur un int
est atomique entre yield points. La cohérence du snapshot est
best-effort (lecture multi-champ peut surprendre un incr en cours)
— acceptable pour /stats en démo ; pas un compteur facturation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Stats:
    started_at_iso: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at_monotonic: float = field(default_factory=lambda: __import__("time").monotonic())
    total_turns: int = 0
    total_tool_calls: int = 0
    pappers_calls_today: int = 0
    pappers_calls_today_day: str = field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%d")
    )
    anthropic_input_tokens: int = 0
    anthropic_output_tokens: int = 0
    errors: int = 0


_stats = Stats()


def _rollover_day_if_needed() -> None:
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    if _stats.pappers_calls_today_day != today:
        _stats.pappers_calls_today = 0
        _stats.pappers_calls_today_day = today


def incr(**kwargs: int) -> None:
    """Incrémente atomiquement les compteurs nommés.

    Champs reconnus (typo lèvera ``AttributeError`` au runtime) :
    ``total_turns``, ``total_tool_calls``, ``pappers_calls_today``,
    ``anthropic_input_tokens``, ``anthropic_output_tokens``, ``errors``.
    """
    _rollover_day_if_needed()
    for k, v in kwargs.items():
        if not hasattr(_stats, k):
            raise AttributeError(f"unknown stat: {k}")
        setattr(_stats, k, getattr(_stats, k) + v)


def snapshot() -> dict[str, int | str]:
    import time

    _rollover_day_if_needed()
    return {
        "started_at": _stats.started_at_iso,
        "uptime_s": int(time.monotonic() - _stats.started_at_monotonic),
        "total_turns": _stats.total_turns,
        "total_tool_calls": _stats.total_tool_calls,
        "pappers_calls_today": _stats.pappers_calls_today,
        "pappers_calls_today_day": _stats.pappers_calls_today_day,
        "anthropic_input_tokens": _stats.anthropic_input_tokens,
        "anthropic_output_tokens": _stats.anthropic_output_tokens,
        "errors": _stats.errors,
    }


def pappers_calls_today() -> int:
    _rollover_day_if_needed()
    return _stats.pappers_calls_today


def reset_for_tests() -> None:
    """Reset complet — utilisé par la fixture ``_fresh_stats`` du conftest."""
    global _stats
    _stats = Stats()
```

### `observability/credit_guard.py`

```python
"""Mode dégradé cache-only quand le cap crédits Pappers journalier
est atteint (cahier §17.2, R1, R16).

API synchrone : ``mcp_pappers._is_degraded()`` (S02 review C4) résout
``degraded`` une seule fois et l'appelle à chaque ``call_tool``. Aucune
race condition possible (asyncio cooperative + int read).
"""

from __future__ import annotations

from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP
from genial_agent.observability.stats import pappers_calls_today


def degraded() -> bool:
    """``True`` si on doit servir uniquement depuis le cache MCP (S02).

    Consommé par ``genial_agent.mcp_pappers._is_degraded()`` (résolu
    paresseusement via import optionnel — cf. S02 review C4). Dès que
    ce module est importable, S02 le branche.
    """
    return pappers_calls_today() >= DAILY_PAPPERS_CREDITS_CAP


def remaining() -> int:
    return max(0, DAILY_PAPPERS_CREDITS_CAP - pappers_calls_today())
```

### `observability/routes.py`

```python
"""Handlers Starlette pour /health et /stats. Mountés via ``mount.py``.

Note sécurité : aucun champ de ``snapshot()`` ne contient de PII ni
de secret (compteurs d'aggrégats uniquement). L'URL Pappers complète
est server-only par construction (cf. ``mcp_pappers._build_url``).
"""

from __future__ import annotations

import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from genial_agent import __version__, mcp_pappers
from genial_agent.observability.stats import snapshot


async def health(request: Request) -> JSONResponse:  # noqa: ARG001
    """Healthcheck riche : ping MCP Pappers + meta version/uptime.

    Code HTTP **toujours 200** (cf. story phase 1) ; le statut effectif
    est dans ``body["status"]``. UptimeRobot (S08) lira ce champ via
    son keyword check, pas par le code HTTP.
    """
    mcp = await mcp_pappers.healthcheck()
    snap = snapshot()
    return JSONResponse(
        {
            "status": mcp["status"],
            "mcp": mcp,
            "version": __version__,
            "uptime_s": snap["uptime_s"],
        }
    )


async def stats(request: Request) -> JSONResponse:
    """Compteurs cumulatifs. Optionnellement protégé par ``STATS_TOKEN``.

    Si ``STATS_TOKEN`` n'est pas défini → endpoint **ouvert**. C'est le
    choix MVP démo : surface d'attaque minime (compteurs anonymisés),
    et pas de friction pour Fabien qui veut vérifier la consommation.
    En prod, set ``STATS_TOKEN`` dans Railway pour exiger un Bearer.
    """
    token = os.getenv("STATS_TOKEN")
    if token:
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] != token:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return JSONResponse(snapshot())
```

### `observability/mount.py`

```python
"""Prepend des routes /health et /stats sur l'app FastAPI Chainlit.

Le catch-all ``@router.get("/{full_path:path}")`` de Chainlit est
ajouté en dernier au moment du ``import chainlit`` (cf. phase 1
inspection). En prepend-ant nos routes en index 0/1, FastAPI les
matche avant le catch-all.

Idempotent via le flag ``_MOUNTED`` — appeler plusieurs fois est
sans effet (utile dans les tests fixtures).
"""

from __future__ import annotations

from starlette.routing import Route

from genial_agent.observability.routes import health, stats

_MOUNTED = False


def mount_routes() -> None:
    global _MOUNTED
    if _MOUNTED:
        return
    # Import tardif : retarde le coût d'import chainlit jusqu'au boot
    # de l'app (et permet aux tests S07 unit qui ne touchent pas aux
    # routes de ne pas charger chainlit).
    from chainlit.server import app as cl_app

    cl_app.router.routes.insert(0, Route("/health", health, methods=["GET"]))
    cl_app.router.routes.insert(1, Route("/stats", stats, methods=["GET"]))
    _MOUNTED = True


def reset_for_tests() -> None:
    """Force un re-mount au prochain ``mount_routes`` — tests only."""
    global _MOUNTED
    _MOUNTED = False
```

### `observability/__init__.py`

```python
"""Observability S07 — structlog JSON, idempotence, stats, /health, /stats."""

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
```

### Modifications `app.py`

```python
# En tête du fichier, après les imports existants :
import structlog
from genial_agent.observability import (
    cache as idempotence_cache,
    configure_logging,
    mount_routes,
    remaining as credits_remaining,
    incr as stats_incr,
)
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP

configure_logging()  # IDEMPOTENT, doit être appelé avant tout logger.* utile
mount_routes()       # prepend /health et /stats sur chainlit.server.app

# Bandeau "crédits bas" — seuil 10 % cf. cahier §16.3 R16
_CREDITS_LOW_RATIO = 0.10


# Dans on_message, juste après avoir resolu session_id :
async def on_message(message: cl.Message) -> None:
    state: ConversationState = cl.user_session.get("state") or ConversationState()
    session_id: str = _resolve_session_id()

    with structlog.contextvars.bound_contextvars(session_id=session_id):
        # 1. Idempotence — réponse cached < 60 s ?
        cached = await idempotence_cache.get(session_id, message.content)
        if cached is not None:
            logger.info("ui_idempotence_hit")
            await cl.Message(
                content=cached + "\n\n_(réponse servie depuis le cache idempotence)_",
                author="Agent",
            ).send()
            return

        # 2. Bandeau crédits bas (cahier §16.3 — non-bloquant)
        rem = credits_remaining()
        if rem < _CREDITS_LOW_RATIO * DAILY_PAPPERS_CREDITS_CAP:
            logger.warning("ui_credits_low_banner", remaining=rem)
            await cl.Message(
                content=(
                    f"⚠ Budget Pappers dégradé — il reste {rem} appels "
                    f"sur {DAILY_PAPPERS_CREDITS_CAP} aujourd'hui. "
                    f"Mode cache-only sur les entités connues (LVMH, BNP, Carrefour)."
                ),
                author="Système",
                type="system_message",
            ).send()

        # 3. Pipeline normal — code existant inchangé
        msg = cl.Message(content="", author="Agent")
        await msg.send()
        turn_state = TurnState(msg=msg)
        turn_gen = run_guarded_turn(state, message.content, session_id)
        try:
            async for event in turn_gen:
                await dispatch_event(event, turn_state)
        finally:
            await turn_gen.aclose()
            await _drain_orphan_steps(turn_state)

        if turn_state.input_rejected:
            return

        if not turn_state.linkify_applied:
            msg.content = linkify_sirens(msg.content or "")
            turn_state.linkify_applied = True
            turn_state.final_text = msg.content
            await msg.update()

        badge = model_badge(...)  # code existant
        msg.content = (msg.content or "") + f"\n\n---\n*Modèle : {badge}*"
        await msg.update()

        entity = extract_active_entity(turn_state.tracker)
        await _update_entity_banner(entity)

        # 4. Store idempotence pour les 60 prochaines secondes
        if msg.content:
            await idempotence_cache.set(session_id, message.content, msg.content)
```

### Modifications `mcp_pappers.py`

À insérer dans `call_tool` **après** `await cache.set(name, args, payload)`
(ligne 467 actuelle), **avant** `return payload` :

```python
from genial_agent.observability import incr as stats_incr

# ... existing code up to line 467 ...
await cache.set(name, args, payload)
stats_incr(total_tool_calls=1, pappers_calls_today=1)  # S07
return payload
```

**Ne pas incrémenter** sur :
- cache hit (ligne 426 `return cached` — 0 crédit consommé) ;
- mode dégradé (ligne 430 `raise CreditsExhausted` — pas d'appel
  réseau) ;
- business error (ligne 459 `_raise_business_error` — la requête a
  consommé un crédit Pappers en théorie, mais on incrémente pas pour
  rester cohérent avec le ``stats_incr`` qui ne compte que les
  succès comptables).

### Modifications `pipeline.py`

Dans la branche `elif etype == "llm_meta":` (lignes 146-156) :

```python
elif etype == "llm_meta":
    in_tok = int(event.get("input_tokens") or 0)
    out_tok = int(event.get("output_tokens") or 0)
    await budget.add(session_id, in_tok, out_tok)
    # S07 — instrumentation centrale (capture Haiku + Sonnet escalade
    # via le seul point qui voit tous les llm_meta).
    stats_incr(total_turns=1, anthropic_input_tokens=in_tok, anthropic_output_tokens=out_tok)
    request_id = event.get("request_id")
    if request_id:
        structlog.contextvars.bind_contextvars(request_id=request_id)
    if not budget_emitted and await budget.exhausted(session_id):
        # ... existing code unchanged
```

Imports à ajouter en tête de `pipeline.py` :
```python
import structlog
from genial_agent.observability import incr as stats_incr
```

### Modification `.env.example`

Ajouter au bloc Observabilité :

```bash
# ─── Observabilité ────────────────────────────────────────────
LOG_LEVEL=INFO
# Optionnel : si défini, /stats exige Authorization: Bearer <STATS_TOKEN>
# (recommandé en prod Railway). Laisser vide en dev pour curl direct.
# STATS_TOKEN=
```

### Tests à produire

#### `tests/conftest.py` (extension)

Ajouter une fixture autouse pour réinitialiser les compteurs entre
tests S07 :

```python
@pytest.fixture(autouse=True)
def _fresh_stats():
    from genial_agent.observability import stats as s
    s.reset_for_tests()
    yield
    s.reset_for_tests()
```

#### `tests/unit/test_S07_idempotence.py`

```python
import asyncio
import pytest
from genial_agent.observability.idempotence import IdempotenceCache


async def test_hit_within_ttl():
    c = IdempotenceCache(ttl_s=10)
    await c.set("sess1", "hello", "response A")
    assert await c.get("sess1", "hello") == "response A"


async def test_miss_different_session():
    c = IdempotenceCache(ttl_s=10)
    await c.set("sess1", "hello", "A")
    assert await c.get("sess2", "hello") is None


async def test_expires_after_ttl():
    c = IdempotenceCache(ttl_s=0)  # immédiate
    await c.set("sess1", "hello", "A")
    await asyncio.sleep(0.01)
    assert await c.get("sess1", "hello") is None


async def test_lru_eviction_when_full():
    c = IdempotenceCache(ttl_s=60, max_entries=2)
    await c.set("s", "a", "A")
    await c.set("s", "b", "B")
    await c.set("s", "c", "C")  # evicts "a"
    assert await c.get("s", "a") is None
    assert await c.get("s", "b") == "B"
    assert await c.get("s", "c") == "C"


async def test_unicode_message_doesnt_crash():
    c = IdempotenceCache(ttl_s=10)
    msg = "fiche 🇫🇷 LVMH é€"
    await c.set("s", msg, "ok")
    assert await c.get("s", msg) == "ok"
```

#### `tests/unit/test_S07_stats.py`

```python
import pytest
from genial_agent.observability import stats as s


def test_incr_and_snapshot_roundtrip():
    before = s.snapshot()
    s.incr(total_turns=1, total_tool_calls=3, pappers_calls_today=3)
    after = s.snapshot()
    assert after["total_turns"] == before["total_turns"] + 1
    assert after["total_tool_calls"] == before["total_tool_calls"] + 3
    assert after["pappers_calls_today"] == before["pappers_calls_today"] + 3


def test_incr_unknown_field_raises():
    with pytest.raises(AttributeError, match="unknown stat"):
        s.incr(total_nonsense=1)


def test_pappers_today_rollover(monkeypatch):
    s.incr(pappers_calls_today=5)
    assert s.pappers_calls_today() == 5
    # Force un changement de jour côté snapshot via monkeypatch utcnow
    import datetime as dt
    fake_today = dt.datetime.now(dt.UTC) + dt.timedelta(days=1)

    class _FakeDT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fake_today

    monkeypatch.setattr("genial_agent.observability.stats.datetime", _FakeDT)
    # Le 1er accès post-rollover doit reset
    assert s.pappers_calls_today() == 0


def test_uptime_grows():
    snap1 = s.snapshot()
    import time
    time.sleep(0.05)
    snap2 = s.snapshot()
    assert snap2["uptime_s"] >= snap1["uptime_s"]
```

#### `tests/unit/test_S07_credit_guard.py`

```python
from genial_agent.observability import credit_guard, stats as s
from genial_agent.guardrails.caps import DAILY_PAPPERS_CREDITS_CAP


def test_not_degraded_initially():
    assert credit_guard.degraded() is False
    assert credit_guard.remaining() == DAILY_PAPPERS_CREDITS_CAP


def test_degraded_after_cap():
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP)
    assert credit_guard.degraded() is True
    assert credit_guard.remaining() == 0


def test_remaining_below_cap():
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP - 7)
    assert credit_guard.remaining() == 7
    assert credit_guard.degraded() is False


def test_mcp_pappers_resolves_degraded(monkeypatch):
    """Vérifie que la résolution paresseuse de S02 (cf. _is_degraded)
    branche bien notre fonction maintenant qu'observability existe."""
    from genial_agent import mcp_pappers
    mcp_pappers._reset_degraded_cache()
    # Simule cap atteint
    s.incr(pappers_calls_today=DAILY_PAPPERS_CREDITS_CAP)
    assert mcp_pappers._is_degraded() is True
```

#### `tests/unit/test_S07_logging.py`

```python
import io
import json
import logging

import structlog

from genial_agent.observability.logging import configure_logging, reset_for_tests


def test_pii_scrubbed_in_log_output(capsys):
    reset_for_tests()
    configure_logging()
    logger = structlog.get_logger("test_S07")
    logger.info("test_event", email="leak@example.com", phone="06 12 34 56 78")
    captured = capsys.readouterr().out.strip().splitlines()
    line = json.loads(captured[-1])
    assert line["email"] == "[EMAIL]"
    assert line["phone"] == "[PHONE_FR]"
    # event renamé en msg
    assert line["msg"] == "test_event"
    assert "event" not in line


def test_contextvars_merged_into_log(capsys):
    reset_for_tests()
    configure_logging()
    logger = structlog.get_logger("test_S07")
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(session_id="abc", request_id="req-123")
    logger.info("scoped_event")
    captured = capsys.readouterr().out.strip().splitlines()
    line = json.loads(captured[-1])
    assert line["session_id"] == "abc"
    assert line["request_id"] == "req-123"
    structlog.contextvars.clear_contextvars()


def test_iso_timestamp_present(capsys):
    reset_for_tests()
    configure_logging()
    structlog.get_logger().info("ts_event")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert "timestamp" in line
    assert "T" in line["timestamp"]  # ISO 8601


def test_log_level_present(capsys):
    reset_for_tests()
    configure_logging()
    structlog.get_logger().warning("warn_event")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["level"] == "warning"
```

#### `tests/unit/test_S07_routes.py`

```python
import pytest
from fastapi.testclient import TestClient

from genial_agent.observability.mount import mount_routes, reset_for_tests as reset_mount


@pytest.fixture(autouse=True)
def _ensure_mounted():
    reset_mount()
    mount_routes()


@pytest.fixture
def client():
    from chainlit.server import app as cl_app
    return TestClient(cl_app)


def test_health_returns_4_keys_when_mcp_ok(client, monkeypatch):
    async def fake_health():
        return {"status": "ok", "latency_ms": 12, "tools_count": 7, "error": None}
    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body.keys()) >= {"status", "mcp", "version", "uptime_s"}
    assert body["mcp"]["tools_count"] == 7


def test_health_status_ko_when_mcp_ko_but_http_200(client, monkeypatch):
    async def fake_health():
        return {"status": "ko", "latency_ms": 3000, "tools_count": 0, "error": "TimeoutError"}
    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    r = client.get("/health")
    assert r.status_code == 200  # cf. story §"/health contrat"
    assert r.json()["status"] == "ko"


def test_stats_returns_counters(client):
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert {"total_turns", "total_tool_calls", "pappers_calls_today"} <= body.keys()


def test_stats_token_protection(client, monkeypatch):
    monkeypatch.setenv("STATS_TOKEN", "secret-xyz")
    r_no_auth = client.get("/stats")
    assert r_no_auth.status_code == 401
    r_bad = client.get("/stats", headers={"Authorization": "Bearer wrong"})
    assert r_bad.status_code == 401
    r_ok = client.get("/stats", headers={"Authorization": "Bearer secret-xyz"})
    assert r_ok.status_code == 200


def test_health_overrides_chainlit_default(client, monkeypatch):
    """Vérifie que c'est BIEN notre handler qui répond (pas le statique
    Chainlit qui renverrait juste {'status': 'ok'} sans 'mcp')."""
    async def fake_health():
        return {"status": "ok", "latency_ms": 1, "tools_count": 1, "error": None}
    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    body = client.get("/health").json()
    assert "mcp" in body  # absent du handler natif Chainlit
```

#### `tests/unit/test_S07_contextvars_isolation.py`

```python
import asyncio
import json

import structlog

from genial_agent.observability.logging import configure_logging, reset_for_tests


async def test_contextvars_isolation_between_concurrent_turns(capsys):
    """Deux tasks concurrentes posent des session_id différents.
    Chacune doit voir le sien dans ses logs (contextvars task-local).
    """
    reset_for_tests()
    configure_logging()
    logger = structlog.get_logger("test_S07_iso")

    async def turn(sid: str) -> None:
        with structlog.contextvars.bound_contextvars(session_id=sid):
            await asyncio.sleep(0.01)
            logger.info(f"event_for_{sid}")

    await asyncio.gather(turn("alpha"), turn("beta"))
    out = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(line) for line in out]
    by_msg = {p["msg"]: p for p in parsed}
    assert by_msg["event_for_alpha"]["session_id"] == "alpha"
    assert by_msg["event_for_beta"]["session_id"] == "beta"
```

### Commandes de vérification

```bash
make lint
make test-unit          # unit S07 verts (sans clé API)
# Smoke check live :
make run &              # background
curl -s localhost:8000/health | jq
curl -s localhost:8000/stats | jq
kill %1
```

### Commit phase 2

`feat(S07): structlog JSON + idempotence cache + /health + /stats + stats instrumentation`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] **Routes** :
  - `GET /health` retourne `{"status", "mcp", "version", "uptime_s"}`,
    HTTP 200 même si MCP KO.
  - `GET /stats` retourne un JSON avec compteurs **non nuls après un
    premier turn réel** (preuve que l'instrumentation câble bien).
  - Notre `/health` shadow celui de Chainlit (le body contient `mcp`,
    absent du natif). Test `test_health_overrides_chainlit_default`.
  - `/stats` token-gating : 401 sans header / 200 avec bon header
    (test `test_stats_token_protection`).
- [ ] **Instrumentation call-sites** :
  - `mcp_pappers.call_tool` ligne après `cache.set` : `stats_incr(
    total_tool_calls=1, pappers_calls_today=1)` présent. Cache hit
    n'incrémente PAS (vérifié par grep + test).
  - `pipeline.py` branche `elif etype == "llm_meta":` :
    `stats_incr(total_turns=1, anthropic_input_tokens=…,
    anthropic_output_tokens=…)` + `bind_contextvars(request_id=…)`.
  - **Pas** d'instrumentation dans `agent.run_turn` (sinon
    double-comptage en cas d'escalade Sonnet — vérifié par grep).
- [ ] **Logging** :
  - `configure_logging()` appelé une fois au top-level de `app.py`.
  - Tous les logs sont en JSON valide ligne par ligne (smoke `make
    run`).
  - PII scrub fonctionne (test `test_pii_scrubbed_in_log_output`).
  - `EventRenamer(to="msg")` actif : `event` absent du JSON.
  - `merge_contextvars` injecte `session_id` et `request_id` dans
    chaque ligne loggée pendant un turn.
  - Pas de `print()` résiduel dans `src/`.
- [ ] **Idempotence** :
  - `(session_id, sha256(message))` clef. Sessions différentes →
    pas de collision.
  - TTL 60 s. Au-delà, MISS et 2 appels MCP (test à valider en
    intégration via `make run`).
  - LRU eviction quand `max_entries` dépassé.
  - Cache hit → message UI annoté `_(idempotence cache)_`.
- [ ] **Mode dégradé** :
  - `credit_guard.degraded()` lit
    `pappers_calls_today() >= DAILY_PAPPERS_CREDITS_CAP`.
  - `mcp_pappers._is_degraded()` (S02 review C4) résout désormais
    notre fonction (test `test_mcp_pappers_resolves_degraded`).
  - Bandeau "crédits bas" apparaît quand `remaining < 10` (cahier
    §16.3). Test manuel : forcer
    `s.incr(pappers_calls_today=95)` puis rafraîchir le chat.
- [ ] **Sécurité** :
  - Aucun secret n'apparaît dans `/health` ni `/stats`.
  - L'URL Pappers complète n'est jamais loggée (déjà invariant S02,
    on ne régresse pas).
  - `STATS_TOKEN` lu via `os.getenv` directement (pas via
    `settings`) pour permettre un toggle sans redéploiement.
- [ ] **Rollover jour** :
  - `pappers_calls_today` revient à 0 quand la date UTC change
    (test `test_pappers_today_rollover`).
- [ ] **Style** :
  - `ruff check src tests` clean.
  - `ruff format --check src tests` clean.
  - `gitleaks` clean.
  - Pas de TODO/FIXME oubliés.

### Commit phase 3

`review(S07): approved` ou `review(S07): fix — …` + rework.

---

## ✅ Critères d'acceptation

- [ ] `curl -s http://localhost:8000/health | jq` → JSON 4 clés,
      `status` = `"ok"` quand MCP up.
- [ ] `curl -s http://localhost:8000/stats | jq` → JSON compteurs
      **non vides** après un turn réel (fiche LVMH au minimum).
- [ ] Logs en JSON valide ligne par ligne, contiennent `msg`,
      `level`, `timestamp`, et `session_id` / `request_id` quand un
      tour est en cours.
- [ ] PII scrub : un email injecté dans un kwarg de log apparaît en
      `[EMAIL]` dans le JSON.
- [ ] **Idempotence** : 2 appels identiques rapprochés (< 60 s) ne
      créent qu'un seul tour pipeline (vérifiable via
      `stats.snapshot()` — `total_turns` n'augmente que de 1 sur
      2 submits).
- [ ] **Mode dégradé** : en forçant
      `DAILY_PAPPERS_CREDITS_CAP=1` côté env (override temporaire
      `WALL_CLOCK_S_OVERRIDE` style — note : `caps.py` ne supporte
      pas ce override pour `DAILY_PAPPERS_CREDITS_CAP` à ce jour ;
      test alternatif via `s.incr(pappers_calls_today=…)`), un 2ᵉ
      `call_tool` sur un nom non-caché lève
      `CreditsExhausted` ou retourne le cache.
- [ ] **Override `/health`** : `client.get("/health").json()`
      contient bien la clef `mcp` (preuve qu'on a bien shadow le
      handler Chainlit).
- [ ] `gitleaks detect` clean.
- [ ] `make lint` + `make test-unit` verts.

---

## 📦 Done when

- [x] Phase 1 commitée
      (`story(S07): refine — Chainlit prepend routes, structlog 25.5
      chain, sync stats, call-site refs`).
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Ligne S07 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
