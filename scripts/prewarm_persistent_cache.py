"""Pre-warm le cache MCP persistent avec les `(tool, args)` connus des traces.

Contexte (review S09.5 post-fix, 2026-04-25) :

Le serveur MCP Pappers a un bug de routage où certains tools (vérifié :
``comptes-entreprise``) refusent les jetons Pay-As-You-Go quand le pack
mensuel est saturé. La matrice complète testée live dans
``traces/S095_payg_probe_matrix.json`` :

    sirenisateur                   PAYG OK (1 crédit)
    recherche-entreprises          PAYG OK (1)
    cartographie-entreprise        PAYG OK (3)
    comptes-entreprise             PAYG KO  ← seul tool cassé
    recherche-dirigeants           PAYG OK (1+)
    conformite-personne-physique   PAYG OK (0)
    recherche-beneficiaires        habilitation séparée (hors scope)

Stratégie : pour blinder une démo qui dépend de ``comptes-entreprise``
(scénario U3 du cahier), on alimente le cache disque local — qui prend
le relais quand le live refuse — avec les payloads des appels antérieurs.

Limitation des traces existantes : les events ``tool_result`` du JSONL
ne contiennent que les ``content_preview`` tronqués à 200 chars (cf.
agent.py:571), pas le payload complet. Donc on **ne peut pas reconstruire
les payloads ``comptes-entreprise``** à partir des traces.

Ce que ce script fait à la place :

1. Parse les traces ``S095_*.jsonl`` pour extraire la liste unique des
   ``(tool_name, args)`` qui ont été appelés avec succès.
2. Pour chaque tool **PAYG-compatible**, ré-exécute l'appel via
   ``mcp_pappers.call_tool`` avec ``MCP_CACHE_PERSIST_PATH`` actif →
   le payload est rangé dans le cache disque pour 7 jours.
3. **Skip** les appels ``comptes-entreprise`` avec message explicite —
   à pré-warmer manuellement après refill abonnement (le 30/04) en
   relançant ce script avec les jetons abo dispos.
4. Reporte le coût en crédits PAYG consommés.

Usage::

    # Active la persistance + lance le script
    MCP_CACHE_PERSIST_PATH=data/mcp_cache.json \\
        uv run python scripts/prewarm_persistent_cache.py

    # En mode dry-run (liste les appels sans les exécuter) :
    uv run python scripts/prewarm_persistent_cache.py --dry-run

Le fichier ``data/mcp_cache.json`` est gitignoré (cf. ``.gitignore``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import OrderedDict
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

# Matrice live (probe 2026-04-25, cf. traces/S095_payg_probe_matrix.json).
PAYG_COMPATIBLE: set[str] = {
    "sirenisateur",
    "recherche-entreprises",
    "cartographie-entreprise",
    "recherche-dirigeants",
    "conformite-personne-physique",
}

# Tools cassés en PAYG (skip dans ce pre-warm — à relancer post-refill abo).
PAYG_INCOMPATIBLE: set[str] = {
    "comptes-entreprise",
}


def get_balance() -> dict[str, int]:
    key = os.getenv("PAPPERS_API_KEY")
    r = httpx.get(f"https://api.pappers.fr/v2/suivi-jetons?api_token={key}", timeout=15)
    r.raise_for_status()
    return r.json()


def extract_calls_from_traces(trace_dir: Path) -> list[tuple[str, dict]]:
    """Lit tous les .jsonl du dossier traces et extrait les
    ``(tool_name, args)`` uniques qui ont été appelés (event tool_use).

    On dédoublonne par hash canonique des args pour éviter les doublons
    si plusieurs traces ont fait le même appel.
    """
    seen: OrderedDict[str, tuple[str, dict]] = OrderedDict()
    for jsonl in sorted(trace_dir.glob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("type") != "tool_use":
                continue
            name = ev.get("name")
            args = ev.get("input", {})
            if not isinstance(name, str) or not isinstance(args, dict):
                continue
            # Skip les tools locaux du payload vault
            if name in {"payload_inspect", "payload_search", "escalate_to_sonnet"}:
                continue
            # Clé canonique pour dédoublonner
            key = f"{name}:{json.dumps(args, sort_keys=True, default=str)}"
            if key not in seen:
                seen[key] = (name, args)
    return list(seen.values())


async def prewarm_one(name: str, args: dict, dry_run: bool = False) -> tuple[str, int, str]:
    """Ré-exécute un appel pour le persister dans le cache disque.

    Returns:
        ``(status, cost_payg, message)`` où status ∈
        {"ok", "skip_incompatible", "cached_already", "error"}.
    """
    from genial_agent.mcp_cache import cache
    from genial_agent.mcp_pappers import call_tool

    if name in PAYG_INCOMPATIBLE:
        return ("skip_incompatible", 0, "tool refuse PAYG (à relancer post-refill abo)")

    # Si déjà en cache disk (loaded au boot), ne pas ré-appeler
    cached = await cache.get(name, args)
    if cached is not None:
        return ("cached_already", 0, "déjà en cache disk")

    if dry_run:
        return ("dry_run", 0, "would call (dry-run)")

    bal_before = get_balance()
    try:
        await call_tool(name, args)
    except Exception as exc:  # noqa: BLE001
        return ("error", 0, f"{type(exc).__name__}: {str(exc)[:120]}")
    bal_after = get_balance()
    cost = bal_before["jetons_pay_as_you_go_restants"] - bal_after["jetons_pay_as_you_go_restants"]
    return ("ok", cost, "cached")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lister les appels sans les exécuter (pas de crédits consommés).",
    )
    parser.add_argument(
        "--trace-dir",
        type=Path,
        default=ROOT / "traces",
        help="Dossier des JSONL traces (défaut: traces/).",
    )
    args = parser.parse_args()

    if not os.getenv("MCP_CACHE_PERSIST_PATH") and not args.dry_run:
        print(
            "⚠ MCP_CACHE_PERSIST_PATH non défini : le cache restera in-memory only.\n"
            "  Lance avec : MCP_CACHE_PERSIST_PATH=data/mcp_cache.json uv run python scripts/prewarm_persistent_cache.py",
            file=sys.stderr,
        )
        return 1

    calls = extract_calls_from_traces(args.trace_dir)
    print(f"=== Calls uniques extraits des traces ({len(calls)}) ===")
    for name, a in calls:
        marker = "✅" if name in PAYG_COMPATIBLE else ("❌" if name in PAYG_INCOMPATIBLE else "?")
        print(f"  {marker} {name:<35} {json.dumps(a, ensure_ascii=False)[:80]}")
    print()

    if args.dry_run:
        print("(dry-run, no actual calls made)")
        return 0

    bal_start = get_balance()
    print("=== Solde initial ===")
    print(json.dumps(bal_start, indent=2, ensure_ascii=False))
    print()

    results: list[dict] = []
    for name, a in calls:
        status, cost, msg = await prewarm_one(name, a)
        symbol = {
            "ok": "✅",
            "skip_incompatible": "⏭",
            "cached_already": "💾",
            "error": "❌",
            "dry_run": "🔍",
        }.get(status, "?")
        print(f"{symbol} {name:<35} status={status:<20} cost={cost:>2} {msg}")
        results.append({"tool": name, "args": a, "status": status, "cost_payg": cost, "msg": msg})

    bal_end = get_balance()
    print()
    print("=== Solde final ===")
    print(json.dumps(bal_end, indent=2, ensure_ascii=False))
    consumed = bal_start["jetons_pay_as_you_go_restants"] - bal_end["jetons_pay_as_you_go_restants"]
    print(f"\nTotal PAYG consommés : {consumed}")
    print(f"Cache disk : {os.getenv('MCP_CACHE_PERSIST_PATH')}")

    n_ok = sum(1 for r in results if r["status"] == "ok")
    n_skip = sum(1 for r in results if r["status"] == "skip_incompatible")
    n_cached = sum(1 for r in results if r["status"] == "cached_already")
    n_err = sum(1 for r in results if r["status"] == "error")
    print(
        f"\nBilan : {n_ok} cached, {n_cached} déjà en cache, {n_skip} skipés (PAYG-KO), {n_err} erreurs"
    )

    if n_skip > 0:
        print(
            "\n⚠ Pour pré-warmer les tools PAYG-incompatibles "
            "(`comptes-entreprise`), relance ce script après le refill "
            "abonnement Pappers (le 30/04). Les jetons abonnement les "
            "couvrent eux."
        )

    return 0 if n_err == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
