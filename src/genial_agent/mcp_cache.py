"""Cache tool-level MCP Pappers : (tool_name, args_hash) → result, TTL 24 h.

Couvre deux besoins :
- Protéger les crédits Pappers en dev (itérations rapides sur LVMH/BNP/Carrefour).
- Support du mode dégradé cache-only quand le cap crédits journalier est
  atteint (cf. S07 + cahier §17.2).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TTL_S = 24 * 3600  # 24 h, cahier §5.4


@dataclass
class _Entry:
    value: dict[str, Any]
    expires_at: float


class ToolCache:
    def __init__(self, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._ttl = ttl_s
        self._store: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(tool_name: str, args: dict[str, Any]) -> str:
        args_canon = json.dumps(args, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(args_canon.encode()).hexdigest()[:16]
        return f"{tool_name}:{h}"

    async def get(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        k = self.key(tool_name, args)
        async with self._lock:
            entry = self._store.get(k)
            if entry is None:
                return None
            if entry.expires_at < time.monotonic():
                del self._store[k]
                return None
            logger.info("mcp_cache_hit", tool_name=tool_name)
            return entry.value

    async def set(self, tool_name: str, args: dict[str, Any], value: dict[str, Any]) -> None:
        k = self.key(tool_name, args)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)

    async def contains(self, tool_name: str, args: dict[str, Any]) -> bool:
        return (await self.get(tool_name, args)) is not None


cache = ToolCache()
