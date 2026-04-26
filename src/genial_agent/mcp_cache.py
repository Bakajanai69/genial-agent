"""Cache tool-level MCP Pappers : (tool_name, args_hash) → result, TTL bumpé 7 j.

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

**Persistance disque optionnelle (review S09.5 post-fix, 2026-04-25)** :

- Le constructeur accepte ``persist_path`` : si défini, le cache charge
  au boot un JSON de la forme ``{key: {value, expires_at_epoch}}`` et
  écrit à chaque ``set()`` (write-through). Survit aux redémarrages
  Railway et au cold start. Désactivé par défaut (rétrocompatibilité S02).
- Le timestamp d'expiration bascule de ``time.monotonic()`` (in-RAM)
  vers ``time.time()`` epoch pour permettre le round-trip disque. Drift
  NTP marginal sur un TTL 7 jours.
- TTL bumpé 24 h → 7 jours pour absorber les fenêtres de blocage côté
  serveur Pappers (e.g. bug PAYG sur ``comptes-entreprise`` constaté le
  2026-04-25 — détail dans ``docs/pappers-mcp.md`` §4).

Cas d'usage du cache disque :

1. Pre-warm post-refill abonnement (le 30/04) qui produit un fichier
   ``data/mcp_cache.json`` à reload au boot.
2. Mode dégradé robuste : si l'abonnement est saturé et qu'un tool
   refuse les PAYG (bug), le cache local prend le relais 7 jours sans
   intervention.

Usage::

    cache = ToolCache(persist_path=Path("data/mcp_cache.json"))
    # ou rétrocompatible (in-memory only):
    cache = ToolCache()
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# TTL différencié par tool (S09.6 — D2).
#
# Historique :
# - S02 : TTL 24h uniforme.
# - S09.5 post-fix : bumpé à 7j pour absorber la fenêtre du bug PAYG
#   (`comptes-entreprise` qui refuse les jetons PAYG quand l'abo est vide).
# - S09.6 : retour à 24h par défaut + override 7j sur les tools "snapshots"
#   annuels et lourds en crédits (`comptes-entreprise`, `cartographie-entreprise`)
#   qui ne bougent que quelques fois par an. Les tools "live" (sirenisateur,
#   recherche-entreprises, recherche-dirigeants, conformite-personne-physique)
#   peuvent bouger plus souvent (changement de raison sociale, fusion,
#   nouveaux mandats) → 24h pour rester à jour sans gaspiller le cache.
DEFAULT_TTL_S = 24 * 3600  # 24h (tools "live")
TOOL_TTL_OVERRIDES: dict[str, int] = {
    # Tools "snapshots" annuels — données peu volatiles, lourds en crédits.
    # 7j absorbe les fenêtres de blocage abo / bug PAYG côté Pappers.
    "comptes-entreprise": 7 * 24 * 3600,
    "cartographie-entreprise": 7 * 24 * 3600,
}
DEFAULT_MAX_SIZE = 1024  # cf. review S02 C5


@dataclass
class _Entry:
    value: dict[str, Any]
    # Epoch UNIX (``time.time()``) — switch depuis ``time.monotonic()``
    # pour rendre le cache persistable sur disque entre redémarrages.
    expires_at: float


class ToolCache:
    """Cache thread-safe (asyncio) LRU + TTL + single-flight.

    Le lock couvre uniquement la mutation de ``_store`` et ``_inflight``,
    pas l'appel réseau du single-flight — sinon une requête lente
    bloquerait toutes les autres.
    """

    def __init__(
        self,
        ttl_s: int = DEFAULT_TTL_S,
        max_size: int = DEFAULT_MAX_SIZE,
        persist_path: Path | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be >= 1")
        self._ttl = ttl_s
        self._max_size = max_size
        self._store: OrderedDict[str, _Entry] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        # Persistance disque (review S09.5 post-fix). None = comportement
        # historique in-memory only (rétrocompatibilité S02).
        self._persist_path = persist_path
        if self._persist_path is not None:
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        """Charge les entrées non-expirées depuis ``persist_path``.

        Best-effort : si le fichier est absent, illisible ou contient
        une entrée corrompue, on log et on continue avec un cache vide.
        Une entrée dont ``expires_at`` est passé est silencieusement
        ignorée (pas réécrite — sera purgée au prochain ``_persist``).
        """
        assert self._persist_path is not None
        if not self._persist_path.exists():
            logger.info("mcp_cache_load_skip", reason="file_absent")
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("mcp_cache_load_failed", error=type(exc).__name__)
            return
        now = time.time()
        loaded = 0
        skipped = 0
        for k, entry in data.items():
            try:
                exp = float(entry["expires_at"])
                if exp <= now:
                    skipped += 1
                    continue
                self._store[k] = _Entry(value=entry["value"], expires_at=exp)
                loaded += 1
            except (KeyError, TypeError, ValueError):
                skipped += 1
                continue
        logger.info("mcp_cache_loaded", loaded=loaded, skipped_or_expired=skipped)

    def _persist_to_disk(self) -> None:
        """Réécrit l'intégralité du cache sur disque (write-through).

        Best-effort : un échec d'écriture (disque plein, perms) est
        loggé mais ne propage pas — le cache RAM reste cohérent.
        """
        assert self._persist_path is not None
        snapshot = {
            k: {"value": e.value, "expires_at": e.expires_at} for k, e in self._store.items()
        }
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            # Écriture atomique : tmp puis rename (POSIX). Évite un
            # cache corrompu si on crashe au milieu de l'écriture.
            tmp = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
            tmp.write_text(
                json.dumps(snapshot, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            tmp.replace(self._persist_path)
        except OSError as exc:
            logger.warning("mcp_cache_persist_failed", error=type(exc).__name__)

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
            # ``time.time()`` epoch (review S09.5 post-fix) pour rendre
            # le cache persistable sur disque.
            if entry.expires_at < time.time():
                del self._store[k]
                # Un GET ne déclenche PAS de persist : ça génèrerait des
                # writes parasites et la prochaine écriture (set ou clear)
                # nettoiera de toute façon.
                return None
            # LRU : entrée fraîche → move to end.
            self._store.move_to_end(k)
            logger.info("mcp_cache_hit", tool_name=tool_name)
            return entry.value

    async def set(self, tool_name: str, args: dict[str, Any], value: dict[str, Any]) -> None:
        k = self.key(tool_name, args)
        # TTL différencié par tool (S09.6 — D2). Lookup à chaque set : le
        # mapping est petit (~2 entrées), pas de coût mesurable. Override
        # > self._ttl est intentionnel ici (le TTL d'instance reste le
        # plancher pour les tools sans override explicite).
        ttl = TOOL_TTL_OVERRIDES.get(tool_name, self._ttl)
        async with self._lock:
            self._store[k] = _Entry(value=value, expires_at=time.time() + ttl)
            self._store.move_to_end(k)
            # Éviction LRU si on dépasse la borne (fait dans la boucle
            # pour absorber les inserts multiples lors d'un warmup).
            while len(self._store) > self._max_size:
                evicted_key, _ = self._store.popitem(last=False)
                logger.info("mcp_cache_evict", key=evicted_key)
            # Write-through disque (si activé). Sous le lock pour
            # garantir la cohérence avec _store.
            if self._persist_path is not None:
                self._persist_to_disk()

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
        """Vide le cache et les futures en vol. Utile pour les tests.

        Si la persistance disque est active, on **ne** purge pas le
        fichier disque (c'est intentionnel : ``clear()`` est appelé par
        les fixtures pytest et ne doit pas écraser un cache prod). Pour
        purger le disque, supprimer le fichier ``persist_path``
        manuellement.
        """
        self._store.clear()
        # Annuler les inflight (si jamais un test leak).
        for fut in self._inflight.values():
            if not fut.done():
                fut.cancel()
        self._inflight.clear()


# Instance par défaut : in-memory only (rétrocompatible S02). La
# persistance disque s'active explicitement via la variable d'env
# ``MCP_CACHE_PERSIST_PATH`` (relatif au CWD ou absolu) — utile pour
# Railway (volume persistant) ou un dev qui veut garder son cache au
# redémarrage. Quand non définie, comportement historique inchangé.
import os as _os  # noqa: E402

_persist_env = _os.getenv("MCP_CACHE_PERSIST_PATH")
_persist_path: Path | None = Path(_persist_env) if _persist_env else None
cache = ToolCache(persist_path=_persist_path)
