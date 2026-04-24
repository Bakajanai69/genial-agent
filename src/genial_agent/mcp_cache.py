"""Cache tool-level MCP Pappers : (tool_name, args_hash) → result, TTL 24 h.

Couvre trois besoins :

- Protéger les crédits Pappers en dev (itérations rapides sur LVMH/BNP/Carrefour).
- Support du mode dégradé cache-only quand le cap crédits journalier est
  atteint (cf. S07 + cahier §17.2).
- **Single-flight** : deux ``call_tool`` concurrents sur la même clé
  partagent un unique appel réseau (cf. review S02 C3). Sans ça,
  3 onglets simultanés = 3 crédits consommés pour la même réponse.

Contraintes :

- **LRU borné** (``max_size``) pour éviter l'OOM sur Railway free tier
  (cf. review S02 C5). 1024 entrées par défaut = ~50 Mo si réponse
  Pappers ~50 Ko, largement sous la limite.
- **Hash SHA-256 tronqué 64 bits** : collision attendue à ~4 × 10⁹
  entrées (birthday bound), hors de portée pour un cache MVP.
- **Sérialisation ``args``** : ``json.dumps(default=str)`` pour tolérer
  les objets arbitraires passés par l'agent (datetime, Decimal, Enum…)
  sans crasher la clé.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

DEFAULT_TTL_S = 24 * 3600  # 24 h, cahier §5.4
DEFAULT_MAX_SIZE = 1024  # cf. review S02 C5


@dataclass
class _Entry:
    value: dict[str, Any]
    expires_at: float


class ToolCache:
    """Cache thread-safe (asyncio) LRU + TTL + single-flight.

    Le lock couvre uniquement la mutation de ``_store`` et ``_inflight``,
    pas l'appel réseau du single-flight — sinon une requête lente
    bloquerait toutes les autres.
    """

    def __init__(self, ttl_s: int = DEFAULT_TTL_S, max_size: int = DEFAULT_MAX_SIZE) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._ttl = ttl_s
        self._max_size = max_size
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def key(tool_name: str, args: dict[str, Any]) -> str:
        """Clé canonique. ``default=str`` évite un crash sur les types non
        JSON-natifs (datetime, Enum, Decimal…) — cf. review S02 C7."""
        args_canon = json.dumps(
            args,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
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
            # LRU : entrée fraîche → move to end.
            self._store.move_to_end(k)
            logger.info("mcp_cache_hit", tool_name=tool_name)
            return entry.value

    async def set(self, tool_name: str, args: dict[str, Any], value: dict[str, Any]) -> None:
        k = self.key(tool_name, args)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=time.monotonic() + self._ttl)
            self._store.move_to_end(k)
            # Éviction LRU si on dépasse la borne (fait dans la boucle
            # pour absorber les inserts multiples lors d'un warmup).
            while len(self._store) > self._max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.info("mcp_cache_evict", key=evicted_key)

    async def contains(self, tool_name: str, args: dict[str, Any]) -> bool:
        return (await self.get(tool_name, args)) is not None

    def _inflight_future(self, cache_key: str) -> tuple[asyncio.Future[dict[str, Any]], bool]:
        """Retourne ``(future, owns)`` — ``owns=True`` pour le premier
        caller de cette clé, qui est responsable de peupler le future."""
        fut = self._inflight.get(cache_key)
        if fut is not None:
            return fut, False
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._inflight[cache_key] = fut
        return fut, True

    async def single_flight(
        self,
        tool_name: str,
        args: dict[str, Any],
        producer,  # () -> Awaitable[dict[str, Any]]
    ) -> dict[str, Any]:
        """Garantit qu'une seule ``producer()`` s'exécute à la fois par
        clé. Les appels concurrents sur la même clé attendent le résultat
        du premier. Les erreurs sont propagées identiquement à tous.

        Le cache hit "classique" est géré en amont par ``call_tool`` —
        cette méthode ne consulte que ``_inflight``, pas ``_store``.
        """
        k = self.key(tool_name, args)
        async with self._lock:
            fut, owns = self._inflight_future(k)

        if not owns:
            # Coalesced : on attend le résultat du leader.
            logger.info("mcp_cache_coalesced", tool_name=tool_name)
            return await fut

        try:
            result = await producer()
        except BaseException as exc:  # noqa: BLE001 — on reraise juste après
            fut.set_exception(exc)
            raise
        else:
            fut.set_result(result)
            return result
        finally:
            # Nettoyage synchrone : les waiters ont déjà await-é ``fut``,
            # on peut libérer l'inflight sans risque.
            async with self._lock:
                self._inflight.pop(k, None)

    def clear(self) -> None:
        """Vide le cache et les futures en vol. Utile pour les tests."""
        self._store.clear()
        # Annuler les inflight (si jamais un test leak).
        for fut in self._inflight.values():
            if not fut.done():
                fut.cancel()
        self._inflight.clear()


cache = ToolCache()
