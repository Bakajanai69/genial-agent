"""Tests unitaires du cache tool-level : TTL, hash canonique, LRU bornée,
single-flight, tolérance d'args non-JSON natifs.

Couvre les corrections post-review S02 :
- C3 single-flight (coalescing concurrent cache miss).
- C5 éviction LRU bornée.
- C7 sérialisation d'args avec ``default=str``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from genial_agent.mcp_cache import ToolCache

# ── TTL + miss + canonicalisation ────────────────────────────────────


async def test_cache_hit() -> None:
    c = ToolCache(ttl_s=60)
    await c.set("get_company", {"siren": "775670417"}, {"name": "LVMH"})
    res = await c.get("get_company", {"siren": "775670417"})
    assert res == {"name": "LVMH"}


async def test_cache_miss_returns_none() -> None:
    c = ToolCache(ttl_s=60)
    assert await c.get("unknown", {"x": 1}) is None


async def test_cache_args_canonical_order() -> None:
    """Hash stable peu importe l'ordre des clés — sinon le cache rate."""
    c = ToolCache(ttl_s=60)
    await c.set("foo", {"a": 1, "b": 2}, {"ok": True})
    res = await c.get("foo", {"b": 2, "a": 1})
    assert res == {"ok": True}


async def test_cache_expires() -> None:
    c = ToolCache(ttl_s=0)
    await c.set("foo", {"x": 1}, {"v": 1})
    await asyncio.sleep(0.01)
    assert await c.get("foo", {"x": 1}) is None


async def test_cache_contains() -> None:
    c = ToolCache(ttl_s=60)
    assert await c.contains("foo", {"x": 1}) is False
    await c.set("foo", {"x": 1}, {"v": 1})
    assert await c.contains("foo", {"x": 1}) is True


def test_key_stable_and_sorted() -> None:
    assert ToolCache.key("foo", {"a": 1, "b": 2}) == ToolCache.key("foo", {"b": 2, "a": 1})
    # Tool name est un namespace — deux tools différents, même args → clés
    # différentes.
    assert ToolCache.key("foo", {"x": 1}) != ToolCache.key("bar", {"x": 1})


# ── S09.7 : canonicalisation des listes de scalaires ─────────────────


def test_key_lists_of_scalars_order_invariant() -> None:
    """L'ordre des éléments dans une liste de scalaires (ex:
    ``return_fields=[...]``) ne doit pas changer la clé.

    Régression : observation live LVMH conv1/conv2 (2026-04-26) où
    l'agent Haiku varie l'ordre de ``return_fields`` entre 2 sessions
    → cache miss → 1 PAYG payé en double pour la même donnée.
    """
    args_a = {"siren": "775670417", "return_fields": ["a", "b", "c"]}
    args_b = {"siren": "775670417", "return_fields": ["c", "a", "b"]}
    assert ToolCache.key("recherche-entreprises", args_a) == ToolCache.key(
        "recherche-entreprises", args_b
    )


def test_key_lists_of_scalars_dedup() -> None:
    """Les doublons dans une liste de scalaires sont éliminés.
    ``["a", "a", "b"]`` → même clé que ``["a", "b"]``."""
    k_dup = ToolCache.key("foo", {"return_fields": ["a", "a", "b"]})
    k_uniq = ToolCache.key("foo", {"return_fields": ["a", "b"]})
    assert k_dup == k_uniq


def test_key_lists_of_dicts_order_preserved() -> None:
    """Les listes contenant des **dicts** (ou autres objets composés)
    gardent leur ordre — peut porter un sens (étapes, pagination).
    Aucun tool Pappers retenu n'est dans ce cas, mais on garde la
    safety."""
    args_a = {"steps": [{"step": 1}, {"step": 2}]}
    args_b = {"steps": [{"step": 2}, {"step": 1}]}
    assert ToolCache.key("foo", args_a) != ToolCache.key("foo", args_b)


def test_key_subset_still_distinct() -> None:
    """Un vrai sous-ensemble de ``return_fields`` doit produire une
    clé distincte (l'agent demande moins de champs → réponse Pappers
    différente, donc cache différent légitime)."""
    full = ToolCache.key("foo", {"return_fields": ["a", "b", "c"]})
    subset = ToolCache.key("foo", {"return_fields": ["a", "b"]})
    assert full != subset


def test_key_canonicalize_handles_mixed_types() -> None:
    """Listes mixtes ``[1, "1"]`` ne doivent pas crasher (pattern
    rare mais possible si le LLM mixe entier/string)."""
    k = ToolCache.key("foo", {"items": [1, "1", 2, "2"]})
    assert isinstance(k, str)
    assert k.startswith("foo:")


def test_key_canonicalize_empty_list_no_crash() -> None:
    """Liste vide → clé valide, pas de tri à faire."""
    k = ToolCache.key("foo", {"return_fields": []})
    assert isinstance(k, str)


def test_key_canonicalize_nested_dict_with_list() -> None:
    """Récursion dict → list : la canonicalisation descend dans les
    sous-niveaux (ex: ``{"filters": {"tags": ["b","a"]}}``)."""
    k_a = ToolCache.key("foo", {"filters": {"tags": ["b", "a"]}})
    k_b = ToolCache.key("foo", {"filters": {"tags": ["a", "b"]}})
    assert k_a == k_b


# ── LRU bounded size (review C5) ─────────────────────────────────────


async def test_cache_evicts_oldest_when_full() -> None:
    """Review C5 : au-delà de ``max_size``, l'entrée LRU est évincée.
    Protège la RAM sur Railway free tier."""
    c = ToolCache(ttl_s=60, max_size=2)
    await c.set("t", {"i": 1}, {"v": 1})
    await c.set("t", {"i": 2}, {"v": 2})
    # Trigger éviction : {i:1} est la plus ancienne.
    await c.set("t", {"i": 3}, {"v": 3})
    assert await c.get("t", {"i": 1}) is None
    assert await c.get("t", {"i": 2}) == {"v": 2}
    assert await c.get("t", {"i": 3}) == {"v": 3}


async def test_cache_lru_promotes_on_get() -> None:
    """Un ``get`` rend l'entrée 'fraîche' → elle n'est pas évincée au
    prochain ``set``."""
    c = ToolCache(ttl_s=60, max_size=2)
    await c.set("t", {"i": 1}, {"v": 1})
    await c.set("t", {"i": 2}, {"v": 2})
    # Touch i=1 → i=2 devient la plus ancienne.
    await c.get("t", {"i": 1})
    await c.set("t", {"i": 3}, {"v": 3})
    assert await c.get("t", {"i": 1}) == {"v": 1}
    assert await c.get("t", {"i": 2}) is None
    assert await c.get("t", {"i": 3}) == {"v": 3}


def test_max_size_must_be_positive() -> None:
    with pytest.raises(ValueError):
        ToolCache(max_size=0)


# ── JSON-safe args (review C7) ───────────────────────────────────────


def test_key_tolerates_non_json_types() -> None:
    """Review C7 : ``datetime`` / ``set`` / ``Decimal`` ne doivent pas
    faire crasher la construction de clé — ``default=str`` convertit
    sans bruit."""
    # Avant C7, ça levait `TypeError: Object of type datetime is not JSON serializable`.
    k = ToolCache.key("t", {"when": datetime(2026, 4, 24)})
    assert isinstance(k, str) and k.startswith("t:")


# ── Single-flight (review C3) ────────────────────────────────────────


async def test_single_flight_coalesces_concurrent_producers() -> None:
    """Deux appelants sur la même clé partagent un seul producer. Impact
    crédits Pappers : 3 onglets simultanés = 1 crédit au lieu de 3."""
    c = ToolCache(ttl_s=60)
    calls = 0

    async def _producer() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return {"v": 42}

    r1, r2, r3 = await asyncio.gather(
        c.single_flight("t", {"x": 1}, _producer),
        c.single_flight("t", {"x": 1}, _producer),
        c.single_flight("t", {"x": 1}, _producer),
    )
    assert r1 == r2 == r3 == {"v": 42}
    assert calls == 1


async def test_single_flight_distinct_keys_are_independent() -> None:
    c = ToolCache(ttl_s=60)
    calls = 0

    async def _producer_factory(val: int):
        async def _p() -> dict[str, int]:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return {"v": val}

        return _p

    r1, r2 = await asyncio.gather(
        c.single_flight("t", {"x": 1}, await _producer_factory(1)),
        c.single_flight("t", {"x": 2}, await _producer_factory(2)),
    )
    assert r1 == {"v": 1}
    assert r2 == {"v": 2}
    assert calls == 2


async def test_single_flight_propagates_exception_to_all_waiters() -> None:
    """Si le leader plante, tous les waiters reçoivent la même exception —
    personne ne reste bloqué ou n'obtient un résultat fantôme."""
    c = ToolCache(ttl_s=60)

    async def _bomb() -> dict[str, int]:
        await asyncio.sleep(0.02)
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.gather(
            c.single_flight("t", {"x": 1}, _bomb),
            c.single_flight("t", {"x": 1}, _bomb),
        )

    # Et surtout, l'entrée inflight doit avoir été libérée pour que le
    # prochain appelant reparte sur un nouveau producer (pas collé à
    # l'erreur précédente).
    async def _ok() -> dict[str, int]:
        return {"v": 1}

    res = await c.single_flight("t", {"x": 1}, _ok)
    assert res == {"v": 1}


# ── clear() helper for tests ─────────────────────────────────────────


async def test_cache_clear_empties_store() -> None:
    c = ToolCache(ttl_s=60)
    await c.set("t", {"x": 1}, {"v": 1})
    c.clear()
    assert await c.get("t", {"x": 1}) is None


# ── Persistance disque (S09.5 post-fix, 2026-04-25) ──────────────────


async def test_cache_persist_round_trip(tmp_path) -> None:
    """Une entrée écrite par un cache persistent est rechargée par un
    second cache pointant sur le même fichier — survit au redémarrage
    process simulé."""
    persist = tmp_path / "cache.json"
    c1 = ToolCache(ttl_s=60, persist_path=persist)
    await c1.set("get_company", {"siren": "775670417"}, {"name": "LVMH"})
    assert persist.exists(), "le fichier de persistance doit être créé"

    # Nouveau cache (simule redémarrage process)
    c2 = ToolCache(ttl_s=60, persist_path=persist)
    res = await c2.get("get_company", {"siren": "775670417"})
    assert res == {"name": "LVMH"}


async def test_cache_persist_drops_expired_on_load(tmp_path) -> None:
    """Une entrée expirée dans le fichier disque n'est pas rechargée."""
    persist = tmp_path / "cache.json"
    # TTL 0 → l'entrée expire immédiatement (pas en pratique mais ok pour
    # forcer le scénario en test).
    c1 = ToolCache(ttl_s=0, persist_path=persist)
    await c1.set("t", {"x": 1}, {"v": 1})
    await asyncio.sleep(0.01)
    # Reload : l'entrée doit être ignorée (expirée).
    c2 = ToolCache(ttl_s=60, persist_path=persist)
    assert await c2.get("t", {"x": 1}) is None


async def test_cache_persist_tolerates_missing_file(tmp_path) -> None:
    """Si ``persist_path`` n'existe pas au boot, on démarre simplement
    avec un cache vide — pas d'exception."""
    persist = tmp_path / "absent.json"
    assert not persist.exists()
    c = ToolCache(ttl_s=60, persist_path=persist)
    # Le cache est utilisable normalement
    await c.set("t", {"x": 1}, {"v": 1})
    assert await c.get("t", {"x": 1}) == {"v": 1}
    # Et on a bien créé le fichier au premier set()
    assert persist.exists()


async def test_cache_persist_tolerates_corrupt_file(tmp_path) -> None:
    """Un fichier corrompu (non-JSON) ne fait pas planter le boot — on
    démarre vide et la prochaine écriture restaure un fichier valide."""
    persist = tmp_path / "corrupt.json"
    persist.write_text("{not really json", encoding="utf-8")
    c = ToolCache(ttl_s=60, persist_path=persist)
    await c.set("t", {"x": 1}, {"v": 1})
    assert await c.get("t", {"x": 1}) == {"v": 1}


async def test_cache_persist_atomic_write(tmp_path) -> None:
    """Pas de fichier .tmp orphelin après un set() réussi."""
    persist = tmp_path / "cache.json"
    c = ToolCache(ttl_s=60, persist_path=persist)
    await c.set("t", {"x": 1}, {"v": 1})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == [], f"tmp file not cleaned up: {leftovers}"


async def test_cache_persist_skips_invalid_entries_on_load(tmp_path) -> None:
    """Une entrée mal formée dans le fichier disque est skippée
    silencieusement, sans planter le chargement des autres entrées."""
    import json as _json
    import time as _time

    persist = tmp_path / "cache.json"
    persist.write_text(
        _json.dumps(
            {
                "valid:abcd": {
                    "value": {"v": "ok"},
                    "expires_at": _time.time() + 3600,
                },
                "invalid:nope": "not a dict at all",
                "missing_keys:xyz": {"value": {"v": "missing_exp"}},
            }
        ),
        encoding="utf-8",
    )
    c = ToolCache(ttl_s=60, persist_path=persist)
    # Seule l'entrée valide a été chargée.
    assert len(c._store) == 1


async def test_cache_no_persist_path_means_in_memory_only() -> None:
    """Rétrocompat S02 : sans ``persist_path``, le cache reste in-memory
    pur (aucune écriture disque)."""
    c = ToolCache(ttl_s=60)  # pas de persist_path
    await c.set("t", {"x": 1}, {"v": 1})
    # Pas d'erreur, le set() ne tente pas d'écrire (c._persist_path est None)
    assert c._persist_path is None


def test_default_ttl_is_24h() -> None:
    """TTL par défaut S09.6 : 24h pour les tools "live" (sirenisateur,
    recherche-entreprises, recherche-dirigeants, conformite-…). Les tools
    "snapshots" annuels (`comptes-entreprise`, `cartographie-entreprise`)
    gardent 7j via ``TOOL_TTL_OVERRIDES``.
    """
    from genial_agent.mcp_cache import DEFAULT_TTL_S

    assert DEFAULT_TTL_S == 24 * 3600
