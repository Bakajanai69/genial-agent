"""Cache applicatif ``(session_id, sha256(message)) → réponse texte``.

Pattern aligné sur ``mcp_cache.ToolCache`` (S02) : ``asyncio.Lock``,
deadline ``monotonic``, cleanup paresseux + LRU borné.

TTL court (60 s) : absorbe double-submit, refresh navigateur, retry
utilisateur, sans bloquer une vraie 2ᵉ question identique posée plus
tard (un évaluateur qui repose la même fiche LVMH 2 min plus tard
attend de voir l'agent tourner — un cache long lui ferait croire à
une démo cassée).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from dataclasses import dataclass
from time import monotonic

DEFAULT_TTL_S = 60
MAX_ENTRIES = 256  # ~10 sessions × 20 messages, marge confortable pour la démo


@dataclass
class _Entry:
    value: str
    expires_at: float


class IdempotenceCache:
    """Cache LRU + TTL des réponses agent par ``(session_id, hash(msg))``."""

    def __init__(self, ttl_s: int = DEFAULT_TTL_S, max_entries: int = MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
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
            self._store.move_to_end(k)  # LRU sur lecture
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
