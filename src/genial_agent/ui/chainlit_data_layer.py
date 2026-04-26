"""S09.6 (H3') — Custom Chainlit data layer SQLite anonymous-user.

Backend SQLite via ``aiosqlite`` (async) ; pas d'auth, pas de S3, pas de
PostgreSQL. Persiste les threads + steps + feedbacks pour que la sidebar
Chainlit (liste des conversations précédentes) survive aux redémarrages
du serveur tant que le fichier ``data/cl_threads.db`` survit (bake
Docker + volume Railway, cf. ``data_bootstrap.py``).

Tous les threads sont attachés à un unique utilisateur ``anonymous``
(constante ``ANONYMOUS_USER_ID``). C'est suffisant pour une démo
single-tenant sans login (cf. validation phase 1 — Chainlit issue
#2230). Si un jour le projet ajoute une vraie authentification,
``get_user`` peut être étendu trivialement.

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

import json
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

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_SCHEMA_SQL)
            await self._conn.commit()
            logger.info("chainlit_data_layer_init", db_path=self._db_path)
        return self._conn

    # ── Users ────────────────────────────────────────────────────────

    async def get_user(self, identifier: str) -> PersistedUser | None:
        """Toujours retourne le user ``anonymous`` constant — pas d'auth.

        Chainlit appelle ce hook en lookup avant ``create_user``. On
        renvoie un user fictif déjà persisté pour court-circuiter le flow
        d'inscription.
        """
        return PersistedUser(
            id=ANONYMOUS_USER_ID,
            identifier=ANONYMOUS_USER_ID,
            createdAt=_now_iso(),
            display_name="Utilisateur",
            metadata={},
        )

    async def create_user(self, user: User) -> PersistedUser | None:
        return await self.get_user(user.identifier)

    # ── Threads ──────────────────────────────────────────────────────

    async def get_thread(self, thread_id: str) -> ThreadDict | None:
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM threads WHERE id = ?",
            (thread_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
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
        créer un thread (pas de ``create_thread`` distinct dans l'API)."""
        conn = await self._get_conn()
        now = _now_iso()
        async with conn.execute("SELECT id FROM threads WHERE id = ?", (thread_id,)) as cur:
            exists = await cur.fetchone()

        if exists is None:
            await conn.execute(
                "INSERT INTO threads "
                "(id, name, user_id, user_identifier, tags, metadata, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    name,
                    user_id or ANONYMOUS_USER_ID,
                    ANONYMOUS_USER_ID,
                    _dump_json(tags),
                    _dump_json(metadata),
                    now,
                    now,
                ),
            )
        else:
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
        appliqué si présent ; ``feedback`` et ``userId`` sont ignorés
        (un seul user, pas de feedback granulaire).
        """
        conn = await self._get_conn()
        limit = max(1, pagination.first or 20)

        sql = "SELECT * FROM threads WHERE 1=1"
        params: list[Any] = []
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
        erreur (Chainlit peut re-rejouer un delete au reload page)."""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM steps WHERE thread_id = ?", (thread_id,))
        await conn.execute("DELETE FROM feedbacks WHERE thread_id = ?", (thread_id,))
        await conn.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        await conn.commit()

    async def get_thread_author(self, thread_id: str) -> str:
        return ANONYMOUS_USER_ID

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
