"""Pre-warm les payloads ``comptes-entreprise`` sur les entités golden.

Variant de ``prewarm_persistent_cache.py`` qui **n'exclut PAS** le tool
``comptes-entreprise`` — à lancer manuellement au refill abonnement
(le 30/04 puis mensuellement) quand le pack mensuel Pappers est de
nouveau disponible. Les jetons abonnement couvrent ce tool, contrairement
aux PAYG qui sont refusés (bug serveur Pappers, cf. docs/pappers-mcp.md
§4.2).

Coût attendu : 4 entités × 3 années × 2 crédits/appel = **24 crédits abo
au pire**. Si une (entité, année) est déjà en cache, l'appel est skippé.

Usage::

    MCP_CACHE_PERSIST_PATH=data/mcp_cache.json \\
        uv run python scripts/prewarm_comptes_entreprise.py

Output → ``data/mcp_cache.json`` (versionné, S09.6 — bake Docker).
Après run, commit le diff pour propager le cache enrichi à Railway au
prochain build.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

# Entités golden de la démo (cf. cahier §3 + EVALUATION.md). 4 entités
# couvrent U1–U5 et le pack adversarial.
ENTITIES: list[tuple[str, str]] = [
    ("LVMH", "775670417"),
    ("BNP Paribas", "662042449"),
    ("Carrefour Hyper", "451321335"),
    ("Casino Guichard", "554501171"),
]
# 3 dernières années — couvre U3 "comparaison sur 3 ans".
YEARS: list[int] = [2022, 2023, 2024]


def get_balance() -> dict[str, int] | None:
    """Lit le solde Pappers via le sidecar HTTP gratuit
    ``/v2/suivi-jetons``. Retourne ``None`` si la clé n'est pas dispo
    (mode CI / dry-run sans .env)."""
    key = os.getenv("PAPPERS_API_KEY")
    if not key:
        return None
    r = httpx.get(f"https://api.pappers.fr/v2/suivi-jetons?api_token={key}", timeout=15)
    r.raise_for_status()
    return r.json()


async def prewarm_one(siren: str, annee: int) -> tuple[str, str]:
    """Ré-exécute un appel ``comptes-entreprise(siren, annee)`` pour le
    persister dans le cache disque. Retourne ``(status, message)``."""
    from genial_agent.mcp_cache import cache
    from genial_agent.mcp_pappers import CreditsExhausted, PappersToolError, call_tool

    args = {"siren": siren, "annee": str(annee)}
    cached = await cache.get("comptes-entreprise", args)
    if cached is not None:
        return ("cached_already", "déjà en cache disque")

    try:
        await call_tool("comptes-entreprise", args)
    except CreditsExhausted as exc:
        return ("credits_exhausted", f"abo épuisé : {exc!s:.150}")
    except PappersToolError as exc:
        return ("tool_error", f"{exc.message[:150]}")
    except Exception as exc:  # noqa: BLE001
        return ("error", f"{type(exc).__name__}: {str(exc)[:120]}")
    return ("ok", "cached")


async def main() -> int:
    if not os.getenv("MCP_CACHE_PERSIST_PATH"):
        print(
            "⚠ MCP_CACHE_PERSIST_PATH non défini — le cache sera in-memory only.\n"
            "  Lance avec : MCP_CACHE_PERSIST_PATH=data/mcp_cache.json "
            "uv run python scripts/prewarm_comptes_entreprise.py",
            file=sys.stderr,
        )
        return 1

    bal_start = get_balance()
    if bal_start is not None:
        print("=== Solde initial Pappers ===")
        print(f"  abo restants  : {bal_start.get('jetons_pack_restants', '?')}")
        print(f"  PAYG restants : {bal_start.get('jetons_pay_as_you_go_restants', '?')}")
        print()

    n_ok = 0
    n_cached = 0
    n_credits = 0
    n_err = 0
    for name, siren in ENTITIES:
        for year in YEARS:
            status, msg = await prewarm_one(siren, year)
            symbol = {
                "ok": "✅",
                "cached_already": "💾",
                "credits_exhausted": "💳",
                "tool_error": "⚠",
                "error": "❌",
            }.get(status, "?")
            print(f"{symbol} {name:<20} {year} status={status:<20} {msg}")
            if status == "ok":
                n_ok += 1
            elif status == "cached_already":
                n_cached += 1
            elif status == "credits_exhausted":
                n_credits += 1
            else:
                n_err += 1

    bal_end = get_balance()
    if bal_start is not None and bal_end is not None:
        consumed_abo = bal_start.get("jetons_pack_restants", 0) - bal_end.get(
            "jetons_pack_restants", 0
        )
        print()
        print("=== Solde final Pappers ===")
        print(f"  abo restants  : {bal_end.get('jetons_pack_restants', '?')}")
        print(f"  PAYG restants : {bal_end.get('jetons_pay_as_you_go_restants', '?')}")
        print(f"  abo consommés : {consumed_abo}")

    print(
        f"\nBilan : {n_ok} cached, {n_cached} déjà en cache, "
        f"{n_credits} skipés (crédits épuisés), {n_err} erreurs"
    )

    if n_credits > 0:
        print(
            "\n⚠ Crédits abo épuisés en cours de pre-warm. Relance ce script "
            "le prochain refill mensuel pour compléter le cache.",
            file=sys.stderr,
        )

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
