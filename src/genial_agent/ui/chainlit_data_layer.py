"""S09.6 (H3') — Custom Chainlit data layer SQLite per-session-isolated.

Backend SQLite via ``aiosqlite`` (async) ; pas d'auth, pas de S3, pas de
PostgreSQL. Persiste les threads + steps + feedbacks pour que la sidebar
Chainlit (liste des conversations précédentes) survive aux redémarrages
du serveur tant que le fichier ``data/cl_threads.db`` survit (bake
Docker + volume Railway, cf. ``data_bootstrap.py``).

**Isolation multi-tenant (review S09.6 P1-3)** : chaque session WebSocket
Chainlit reçoit un ``owner_id`` UUID stocké dans ``cl.user_session``
(``session_owner_id``). Tous les threads créés dans cette session sont
attribués à ce ``owner_id`` et le data layer filtre ``list_threads`` /
``get_thread`` / ``delete_thread`` par ce ``owner_id``. Conséquence :
les visiteurs ne voient pas les threads des autres dans la sidebar, et
ne peuvent pas pull un thread d'un voisin via URL trafiquée.

Limites assumées :

- Sans cookie persistant, l'``owner_id`` change à chaque rafraîchissement
  de page → la sidebar redevient vide. Acceptable pour une démo de
  quelques minutes (cf. story §"Critère 'fini'").
- Si ``cl.user_session`` n'est pas accessible (test unit, hors contexte
  Chainlit), on fallback sur ``ANONYMOUS_USER_ID`` constant.
- L'historique des threads "anonymous" pré-fix reste accessible aux
  visiteurs qui ont par hasard ``ANONYMOUS_USER_ID`` (impossible en
  production avec UUID, mais possible en dev / test).

**Hors scope (intentionnel)** :

- Éléments (uploads de fichiers) — désactivés dans
  ``.chainlit/config.toml`` ``features.spontaneous_file_upload.enabled=false``.
  Les méthodes ``create_element`` / ``get_element`` / ``delete_element``
  sont des no-ops pour rester conformes au contrat ``BaseDataLayer``.
- Tags / userId par thread — colonnes présentes mais l'UI démo ne les
  utilise pas.
- Migrations SQL — schéma figé phase 1, pas d'upgrade in-place. Si le
  schéma change, supprimer le fichier ``data/cl_threads.db`` et
  recréer (la dernière session disparaît, c'est documenté comme
  acceptable, cf. story §"Critère 'fini'").

**Critère 'fini'** : ``Ctrl-C`` du serveur Chainlit puis relance →
sidebar peuplée avec les threads précédents → resume au clic.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
import structlog
from chainlit.data.base import BaseDataLayer
from chainlit.types import Feedback, PageInfo, PaginatedResponse, Pagination, ThreadFilter
from chainlit.user import PersistedUser, User

if TYPE_CHECKING:
    from chainlit.element import Element, ElementDict
    from chainlit.step import StepDict
    from chainlit.types import ThreadDict

logger = structlog.get_logger(__name__)

ANONYMOUS_USER_ID = "anonymous"
# Clé sous laquelle ``app.py:on_chat_start`` pose un UUID par session.
# Lue par ``_resolve_owner_id`` pour isoler les threads par visiteur
# (review S09.6 P1-3).
SESSION_OWNER_KEY = "session_owner_id"

# S09.7 hotfix : nom du cookie posé par ``public/owner-cookie.js`` côté
# navigateur (lu/créé depuis ``localStorage`` au pageload). Permet la
# **persistance multi-session** des conversations dans la sidebar
# Chainlit : sans ce cookie, l'``owner_id`` était un UUID éphémère
# par-session-WebSocket → la sidebar redevenait vide à chaque refresh.
OWNER_COOKIE_NAME = "genial_owner_id"
# Pattern conservateur : UUID hex (32 chars) ou format lib JS (avec
# tirets). On exclut les caractères qui pourraient indiquer une
# injection (HTML, quotes, etc.).
_OWNER_COOKIE_RE = re.compile(rf"\b{OWNER_COOKIE_NAME}=([A-Za-z0-9-]{{8,64}})")


def _resolve_owner_id() -> str:
    """Retourne l'``owner_id`` de la session courante, ou
    ``ANONYMOUS_USER_ID`` en fallback (hors contexte Chainlit).

    Ordre de priorité (S09.7 hotfix v5 — ContextVar HTTP middleware) :

    1. **``cl.user_session.get("user").identifier``** — User retourné
       par ``app.py:auth_callback`` (`@cl.header_auth_callback`).
       Disponible UNIQUEMENT en contexte WebSocket actif.
    2. **Cookie HTTP direct via ``cl.context.session.environ``** —
       fallback si l'auth callback n'a pas encore tourné mais on est
       toujours en WebSocket.
    3. **``auth.context.current_owner_id`` ContextVar** — posé par
       ``auth/middleware.py`` sur **chaque** requête HTTP (y compris
       les routes REST que Chainlit appelle hors WebSocket, ex :
       ``/api/thread/<id>`` que la sidebar fetch). Couvre le trou
       entre HTTP middleware et data layer Chainlit.
    4. **``cl.user_session.get(SESSION_OWNER_KEY)``** — UUID éphémère
       par-session-WebSocket, fallback historique S09.6.
    5. **Sentinel ``__no_owner_resolved__``** — hors contexte
       (tests unit, scripts) ou auth pas encore résolue.

    Import tardif de ``chainlit`` : le data layer est importable hors
    contexte Chainlit (tests unit, scripts).
    """
    # 1. User Chainlit standard (posé par auth_callback).
    try:
        import chainlit as cl

        user = cl.user_session.get("user")
        identifier = getattr(user, "identifier", None)
        if isinstance(identifier, str) and identifier:
            logger.info(
                "data_layer_owner_resolved",
                priority=1,
                source="user_session.user",
                identifier_prefix=identifier[:8],
            )
            return identifier
    except Exception:  # noqa: BLE001, S110 — fallback
        pass

    # 2. Cookie HTTP direct (au cas où auth_callback pas encore appliqué).
    try:
        import chainlit as cl

        environ = getattr(cl.context.session, "environ", None) or {}
        cookies = environ.get("HTTP_COOKIE", "")
        if cookies:
            match = _OWNER_COOKIE_RE.search(cookies)
            if match:
                identifier = match.group(1)
                logger.info(
                    "data_layer_owner_resolved",
                    priority=2,
                    source="environ.HTTP_COOKIE",
                    identifier_prefix=identifier[:8],
                )
                return identifier
    except Exception:  # noqa: BLE001, S110
        pass

    # 3. ContextVar posé par ``auth/middleware.py`` sur la requête HTTP
    # courante. Couvre les routes REST hors contexte WebSocket Chainlit
    # (typique : la sidebar fetch ``/api/thread/<id>``) où les sources 1
    # et 2 ne sont pas accessibles.
    try:
        from genial_agent.auth.context import current_owner_id

        ctx_owner = current_owner_id.get()
        if isinstance(ctx_owner, str) and ctx_owner:
            logger.info(
                "data_layer_owner_resolved",
                priority=3,
                source="auth_context_var",
                identifier_prefix=ctx_owner[:8],
            )
            return ctx_owner
    except Exception:  # noqa: BLE001, S110
        pass

    # 4. UUID éphémère par-session-WebSocket (fallback historique S09.6).
    try:
        import chainlit as cl

        owner = cl.user_session.get(SESSION_OWNER_KEY)
        if isinstance(owner, str) and owner:
            logger.info(
                "data_layer_owner_resolved",
                priority=4,
                source="user_session.SESSION_OWNER_KEY",
                identifier_prefix=owner[:8],
            )
            return owner
    except Exception:  # noqa: BLE001, S110
        pass

    # 5. Hors contexte (tests unit, scripts) ou auth pas encore résolue.
    # S09.7 hotfix v4 : on retourne un **sentinel qui ne match aucun
    # thread** (pas ANONYMOUS_USER_ID qui matchait les threads pollués
    # pré-fix v3). Si on retourne ANONYMOUS_USER_ID ici, list_threads
    # appelé hors contexte WebSocket leakerait les threads anonymous
    # à tout le monde.
    logger.info("data_layer_owner_resolved", priority=5, source="sentinel")
    return "__no_owner_resolved__"


# Schéma minimal — 3 tables : threads, steps, feedbacks. Pas de table
# users (un seul utilisateur ``anonymous`` dérivé en runtime). Toutes
# les dates sont stockées en ISO 8601 UTC ("2026-04-26T08:50:00+00:00").
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS threads (
    id TEXT PRIMARY KEY,
    name TEXT,
    user_id TEXT,
    user_identifier TEXT,
    tags TEXT,         -- JSON array
    metadata TEXT,     -- JSON object
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_threads_updated_at ON threads(updated_at DESC);

CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    parent_id TEXT,
    name TEXT,
    type TEXT,
    input TEXT,
    output TEXT,
    metadata TEXT,     -- JSON object
    created_at TEXT,
    start_at TEXT,
    end_at TEXT,
    is_error INTEGER DEFAULT 0,
    raw TEXT NOT NULL  -- JSON full StepDict pour fidélité
);

CREATE INDEX IF NOT EXISTS idx_steps_thread_id ON steps(thread_id, created_at);

CREATE TABLE IF NOT EXISTS feedbacks (
    id TEXT PRIMARY KEY,
    for_id TEXT NOT NULL,
    thread_id TEXT,
    value INTEGER NOT NULL,
    comment TEXT
);

CREATE INDEX IF NOT EXISTS idx_feedbacks_for_id ON feedbacks(for_id);
"""


def _now_iso() -> str:
    """ISO 8601 UTC avec microsecondes.

    On garde la précision µs : le ``ORDER BY updated_at`` de
    ``list_threads`` doit pouvoir départager deux écritures dans la
    même seconde (cas trivial à l'usage : créer 2 conversations rapidement).
    """
    return datetime.now(tz=UTC).isoformat()


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def _load_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return None


class AnonymousSQLiteDataLayer(BaseDataLayer):
    """Data layer SQLite anonymous-user.

    Une seule instance par process Chainlit (le décorateur ``@cl.data_layer``
    cache la factory). Le DB handle est ouvert paresseusement au premier
    appel et réutilisé jusqu'à ``close()``.
    """

    def __init__(self, db_path: str = "data/cl_threads.db") -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        # Review S09.6 P1-2 : protège contre la race où deux requêtes
        # Chainlit hit ``_get_conn`` la 1ère fois en parallèle (boot
        # sidebar + 1er message). Sans lock, ``aiosqlite.connect`` yield
        # le contrôle, les deux tasks voient ``self._conn is None`` et
        # créent chacune une connexion — l'une devient orpheline.
        self._init_lock = asyncio.Lock()

    async def _get_conn(self) -> aiosqlite.Connection:
        # Fast-path sans lock : si la connexion est déjà ouverte, retour
        # immédiat. Le lock ne sert qu'à protéger l'init.
        if self._conn is not None:
            return self._conn
        async with self._init_lock:
            # Double-check : un autre task peut avoir initialisé pendant
            # qu'on attendait le lock.
            if self._conn is None:
                self._conn = await aiosqlite.connect(self._db_path)
                self._conn.row_factory = aiosqlite.Row
                # Review S09.6 P1-4 : WAL pour permettre des reads
                # concurrents pendant un write (cas démo ≥ 2 onglets,
                # cf. cahier §17.4). ``synchronous=NORMAL`` suffit pour
                # un cache de threads (pas de transaction critique).
                await self._conn.execute("PRAGMA journal_mode=WAL;")
                await self._conn.execute("PRAGMA synchronous=NORMAL;")
                await self._conn.executescript(_SCHEMA_SQL)
                await self._conn.commit()
                logger.info("chainlit_data_layer_init", db_path=self._db_path)
        return self._conn

    # ── Users ────────────────────────────────────────────────────────

    async def get_user(self, identifier: str) -> PersistedUser | None:
        """Retourne un PersistedUser avec ``identifier`` = ce que l'auth
        callback a posé (cookie ``genial_owner_id`` ou fallback éphémère
        ``anon-<hex>``).

        S09.7 hotfix v3 : auparavant cette méthode retournait TOUJOURS
        ``ANONYMOUS_USER_ID`` constant, ce qui forçait tous les threads
        créés par Chainlit à user_id="anonymous" → visibles cross-user
        (chaque visiteur voyait les threads de tous les autres). Le fix
        propage l'identifier réel du visiteur (cookie UUID) pour que
        l'isolation par-visiteur fonctionne vraiment.
        """
        return PersistedUser(
            id=identifier,
            identifier=identifier,
            createdAt=_now_iso(),
            display_name="Utilisateur",
            metadata={},
        )

    async def create_user(self, user: User) -> PersistedUser | None:
        return await self.get_user(user.identifier)

    # ── Threads ──────────────────────────────────────────────────────

    async def get_thread(self, thread_id: str) -> ThreadDict | None:
        """Charge un thread, en refusant l'accès aux threads d'un autre
        owner (review S09.6 P1-3). Retourne ``None`` aussi bien sur
        thread inexistant que sur thread d'un autre visiteur — ne pas
        leaker l'existence."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM threads WHERE id = ?",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        # S09.7 hotfix v3 : isolation **stricte** par owner. Un thread
        # n'est lisible que par son owner — plus de "rétrocompat legacy"
        # qui exposait les threads sans user_id à tout le monde (cause
        # de la fuite cross-visiteur observée 2026-04-26).
        owner = _resolve_owner_id()
        thread_owner = row["user_id"]
        if thread_owner != owner:
            logger.info(
                "chainlit_data_layer_thread_access_denied",
                thread_id=thread_id,
                owner_prefix=(owner or "")[:8],
                thread_owner_prefix=(thread_owner or "")[:8],
            )
            return None

        async with conn.execute(
            "SELECT raw FROM steps WHERE thread_id = ? ORDER BY created_at ASC, id ASC",
            (thread_id,),
        ) as cur:
            step_rows = await cur.fetchall()
        steps = [_load_json(r["raw"]) for r in step_rows]
        steps = [s for s in steps if isinstance(s, dict)]

        return {
            "id": row["id"],
            "createdAt": row["created_at"],
            "name": row["name"],
            "userId": row["user_id"] or ANONYMOUS_USER_ID,
            "userIdentifier": row["user_identifier"] or ANONYMOUS_USER_ID,
            "tags": _load_json(row["tags"]) or [],
            "metadata": _load_json(row["metadata"]) or {},
            "steps": steps,
            "elements": [],
        }

    async def update_thread(
        self,
        thread_id: str,
        name: str | None = None,
        user_id: str | None = None,
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """UPSERT pattern — Chainlit appelle ``update_thread`` aussi pour
        créer un thread (pas de ``create_thread`` distinct dans l'API).

        Review S09.6 P1-3 : si ``user_id`` n'est pas fourni explicitement,
        on attribue le thread à l'``owner_id`` de la session courante.
        Sur un UPDATE, on refuse silencieusement les modifs sur un thread
        d'un autre owner (le visiteur n'aurait pas dû y accéder de toute
        façon — son ``get_thread`` aurait retourné ``None``).
        """
        conn = await self._get_conn()
        now = _now_iso()
        owner = _resolve_owner_id()
        effective_user_id = user_id or owner
        logger.info(
            "data_layer_update_thread",
            thread_id=thread_id,
            owner_prefix=(owner or "")[:8],
            effective_user_id_prefix=(effective_user_id or "")[:8],
            user_id_provided=user_id is not None,
        )
        async with conn.execute(
            "SELECT id, user_id FROM threads WHERE id = ?",
            (thread_id,),
        ) as cur:
            exists = await cur.fetchone()

        if exists is None:
            await conn.execute(
                "INSERT INTO threads "
                "(id, name, user_id, user_identifier, tags, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    name,
                    effective_user_id,
                    effective_user_id,
                    _dump_json(tags),
                    _dump_json(metadata),
                    now,
                    now,
                ),
            )
        else:
            # Refus silencieux d'écriture sur un thread d'un autre owner.
            existing_owner = exists["user_id"]
            if existing_owner and existing_owner != owner:
                logger.info(
                    "chainlit_data_layer_thread_update_denied",
                    thread_id=thread_id,
                )
                return
            # Patch partiel : on met à jour seulement les colonnes fournies.
            updates: list[str] = []
            params: list[Any] = []
            if name is not None:
                updates.append("name = ?")
                params.append(name)
            if user_id is not None:
                updates.append("user_id = ?")
                params.append(user_id)
            if metadata is not None:
                updates.append("metadata = ?")
                params.append(_dump_json(metadata))
            if tags is not None:
                updates.append("tags = ?")
                params.append(_dump_json(tags))
            updates.append("updated_at = ?")
            params.append(now)
            params.append(thread_id)
            # Sécurité S608 : ``updates`` est une liste de littéraux statiques
            # (``"name = ?"``, ``"user_id = ?"``, ``"updated_at = ?"`` etc.)
            # construite uniquement à partir de chemins ``if`` côté code,
            # jamais d'input utilisateur. Pas d'injection SQL possible.
            sql = f"UPDATE threads SET {', '.join(updates)} WHERE id = ?"  # noqa: S608
            await conn.execute(sql, params)
        await conn.commit()

    async def list_threads(
        self, pagination: Pagination, filters: ThreadFilter
    ) -> PaginatedResponse[ThreadDict]:
        """Pagination simple : ordonné par updated_at desc, curseur =
        dernier id retourné. Le filtre ``search`` (LIKE sur name) est
        appliqué si présent ; ``feedback`` est ignoré (pas de feedback
        granulaire en démo).

        S09.6 P1-3 + S09.7 hotfix v3 : on filtre **strictement** par
        ``owner_id`` de la session courante. Auparavant on incluait
        aussi les threads ``user_id="anonymous"`` "pour rétrocompat
        legacy" — mais ça causait une fuite cross-visiteur (tous les
        threads créés avant le fix v3 avaient user_id="anonymous"
        à cause d'un bug dans ``get_user``).
        """
        conn = await self._get_conn()
        limit = max(1, pagination.first or 20)
        # S09.7 hotfix v4 : Chainlit passe ``filters.userId`` (l'identifier
        # de l'User retourné par auth_callback). C'est la source de vérité
        # pour le filtrage — plus fiable que ``_resolve_owner_id()`` qui
        # pouvait retourner ``ANONYMOUS_USER_ID`` hors contexte WebSocket
        # → match les threads pollués pré-fix v3 (créés avec
        # user_id="anonymous").
        owner = filters.userId or _resolve_owner_id()
        sql = "SELECT * FROM threads WHERE user_id = ?"
        params: list[Any] = [owner]
        if filters.search:
            sql += " AND name LIKE ?"
            params.append(f"%{filters.search}%")
        # Curseur : on saute jusqu'à l'id donné (ordre updated_at desc).
        if pagination.cursor:
            async with conn.execute(
                "SELECT updated_at FROM threads WHERE id = ?",
                (pagination.cursor,),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                sql += " AND updated_at < ?"
                params.append(row["updated_at"])
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit + 1)  # +1 pour détecter has_next_page

        async with conn.execute(sql, params) as cur:
            rows = await cur.fetchall()

        has_next = len(rows) > limit
        rows = rows[:limit]

        threads: list[ThreadDict] = []
        for row in rows:
            threads.append(
                {
                    "id": row["id"],
                    "createdAt": row["created_at"],
                    "name": row["name"],
                    "userId": row["user_id"] or ANONYMOUS_USER_ID,
                    "userIdentifier": row["user_identifier"] or ANONYMOUS_USER_ID,
                    "tags": _load_json(row["tags"]) or [],
                    "metadata": _load_json(row["metadata"]) or {},
                    "steps": [],
                    "elements": [],
                }
            )

        end_cursor = threads[-1]["id"] if threads else None
        return PaginatedResponse(
            pageInfo=PageInfo(
                hasNextPage=has_next,
                startCursor=threads[0]["id"] if threads else None,
                endCursor=end_cursor,
            ),
            data=threads,
        )

    async def delete_thread(self, thread_id: str) -> None:
        """Idempotent : delete d'un thread inexistant n'est pas une
        erreur (Chainlit peut re-rejouer un delete au reload page).

        Review S09.6 P1-3 : refus silencieux pour un thread d'un autre
        owner. On ne lève pas (Chainlit s'attend à un no-op) mais on log.
        """
        conn = await self._get_conn()
        owner = _resolve_owner_id()
        async with conn.execute(
            "SELECT user_id FROM threads WHERE id = ?",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is not None:
            existing_owner = row["user_id"]
            if existing_owner and existing_owner != owner:
                logger.info(
                    "chainlit_data_layer_thread_delete_denied",
                    thread_id=thread_id,
                )
                return
        await conn.execute("DELETE FROM steps WHERE thread_id = ?", (thread_id,))
        await conn.execute("DELETE FROM feedbacks WHERE thread_id = ?", (thread_id,))
        await conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        await conn.commit()

    async def get_thread_author(self, thread_id: str) -> str:
        """Retourne l'``user_id`` enregistré du thread (review P1-3) — sinon
        l'``ANONYMOUS_USER_ID`` constant pour les threads pré-fix."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT user_id FROM threads WHERE id = ?",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return ANONYMOUS_USER_ID
        return row["user_id"] or ANONYMOUS_USER_ID

    async def delete_user_session(self, id: str) -> bool:  # noqa: A002 — match contrat Chainlit
        """No-op : pas de table de sessions. Retour ``True`` pour signaler
        que l'opération est considérée OK côté API."""
        return True

    # ── Steps ────────────────────────────────────────────────────────

    async def create_step(self, step_dict: StepDict) -> None:
        await self._upsert_step(step_dict)

    async def update_step(self, step_dict: StepDict) -> None:
        await self._upsert_step(step_dict)

    async def _upsert_step(self, step_dict: StepDict) -> None:
        step_id = step_dict.get("id")
        thread_id = step_dict.get("threadId")
        if not step_id or not thread_id:
            return
        conn = await self._get_conn()
        await conn.execute(
            "INSERT INTO steps "
            "(id, thread_id, parent_id, name, type, input, output, metadata, "
            " created_at, start_at, end_at, is_error, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  parent_id=excluded.parent_id, name=excluded.name, type=excluded.type, "
            "  input=excluded.input, output=excluded.output, metadata=excluded.metadata, "
            "  start_at=excluded.start_at, end_at=excluded.end_at, "
            "  is_error=excluded.is_error, raw=excluded.raw",
            (
                step_id,
                thread_id,
                step_dict.get("parentId"),
                step_dict.get("name"),
                step_dict.get("type"),
                step_dict.get("input"),
                step_dict.get("output"),
                _dump_json(step_dict.get("metadata")),
                step_dict.get("createdAt") or _now_iso(),
                step_dict.get("start"),
                step_dict.get("end"),
                int(bool(step_dict.get("isError"))),
                _dump_json(step_dict),
            ),
        )
        # Pousse le thread en tête de la sidebar à chaque update step.
        await conn.execute(
            "UPDATE threads SET updated_at = ? WHERE id = ?",
            (_now_iso(), thread_id),
        )
        await conn.commit()

    async def delete_step(self, step_id: str) -> None:
        conn = await self._get_conn()
        await conn.execute("DELETE FROM steps WHERE id = ?", (step_id,))
        await conn.commit()

    # ── Feedbacks ────────────────────────────────────────────────────

    async def upsert_feedback(self, feedback: Feedback) -> str:
        conn = await self._get_conn()
        feedback_id = feedback.id or f"fb-{feedback.forId}"
        await conn.execute(
            "INSERT INTO feedbacks (id, for_id, thread_id, value, comment) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  for_id=excluded.for_id, thread_id=excluded.thread_id, "
            "  value=excluded.value, comment=excluded.comment",
            (
                feedback_id,
                feedback.forId,
                feedback.threadId,
                int(feedback.value),
                feedback.comment,
            ),
        )
        await conn.commit()
        return feedback_id

    async def delete_feedback(self, feedback_id: str) -> bool:
        conn = await self._get_conn()
        await conn.execute("DELETE FROM feedbacks WHERE id = ?", (feedback_id,))
        await conn.commit()
        return True

    # ── Favorites (no-op : pas de feature favoris en démo) ──────────

    async def set_step_favorite(self, step_dict: StepDict, favorite: bool) -> StepDict:
        """No-op : on ne modélise pas les favoris en démo. On retourne
        le step inchangé pour respecter la signature."""
        return step_dict

    async def get_favorite_steps(self, user_id: str) -> list[StepDict]:
        """Pas de favoris persistés → liste vide."""
        return []

    # ── Elements (no-op : uploads désactivés) ────────────────────────

    async def create_element(self, element: Element) -> None:
        return None

    async def get_element(self, thread_id: str, element_id: str) -> ElementDict | None:
        return None

    async def delete_element(self, element_id: str, thread_id: str | None = None) -> None:
        return None

    # ── Lifecycle ────────────────────────────────────────────────────

    async def build_debug_url(self) -> str:
        return ""

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
