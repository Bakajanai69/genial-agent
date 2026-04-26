"""Probe la compatibilité PAYG des tools MCP Pappers.

Contexte (review S09.5, 2026-04-25) : on a observé live que
``comptes-entreprise`` refuse les jetons PAYG quand le pack abonnement
est épuisé, alors que ``sirenisateur`` les accepte. On veut établir la
matrice complète pour tous les tools retenus avant la démo.

Méthode : pour chaque tool inconnu, faire **un seul appel minimal**
(args ultra-réduits) et observer :

- (a) Succès → PAYG OK + on note le coût (delta solde)
- (b) Refus "crédits insuffisants" → PAYG KO (le tool est abo-only)
- (c) Autre erreur → noter pour debug

Coût max théorique : ~20 crédits PAYG (5 tools × 4 crédits/appel max).
Solde restant après ≥ 70 — confortable.

Usage::

    uv run python scripts/probe_payg_compatibility.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from genial_agent.mcp_pappers import (  # noqa: E402
    CreditsExhausted,
    PappersToolError,
    _invoke_tool_live,
)


def get_balance() -> dict[str, int]:
    """Solde courant via /suivi-jetons (gratuit)."""
    key = os.getenv("PAPPERS_API_KEY")
    r = httpx.get(f"https://api.pappers.fr/v2/suivi-jetons?api_token={key}", timeout=15)
    r.raise_for_status()
    return r.json()


# Args minimaux par tool. Choisis pour minimiser le coût en crédits
# (pas de pagination longue, pas de return_fields exhaustif).
PROBES: list[tuple[str, dict]] = [
    (
        "recherche-entreprises",
        {
            "q": "LVMH",
            "par_page": 1,
            "return_fields": ["nom_entreprise"],
        },
    ),
    (
        "cartographie-entreprise",
        {
            "siren": "775670417",  # LVMH
            "inclure_entreprises_dirigees": False,
            "inclure_entreprises_citees": False,
            "inclure_sci": False,
        },
    ),
    (
        "recherche-dirigeants",
        {
            "q": "Arnault",
            "par_page": 1,
            "return_fields": ["nom"],
        },
    ),
    (
        "conformite-personne-physique",
        {
            "nom": "ARNAULT",
            "prenom": "BERNARD",
            "date_de_naissance": "05-03-1949",  # date publique
        },
    ),
    (
        "recherche-beneficiaires",
        {
            "q": "Arnault",
            "par_page": 1,
        },
    ),
]


async def probe_one(name: str, args: dict) -> dict:
    """Appelle un tool une fois. Retourne {status, cost, error_class, message}."""
    bal_before = get_balance()
    payg_before = bal_before["jetons_pay_as_you_go_restants"]
    abo_before = bal_before["jetons_abonnement"] - bal_before["jetons_abonnement_utilises"]

    result: dict = {
        "tool": name,
        "args": args,
        "payg_before": payg_before,
        "abo_before": abo_before,
    }

    try:
        # _invoke_tool_live est l'appel brut sans wrap business error.
        # On veut voir si Pappers accepte ou refuse l'appel.
        # mais call_tool() fait business_error → exception → on perd le payload.
        # On utilise donc _invoke_tool_live + détection manuelle.
        from genial_agent.mcp_pappers import _extract_business_error

        payload = await _invoke_tool_live(name, args)
        biz = _extract_business_error(payload)
        if biz is not None:
            result["status"] = "business_error"
            result["message"] = biz[:200]
        else:
            result["status"] = "ok"
            # On extrait juste un échantillon pour preuve
            content = payload.get("content")
            if isinstance(content, list) and content:
                first_text = next(
                    (b.get("text", "") for b in content if isinstance(b, dict)),
                    "",
                )
                result["sample"] = first_text[:200]
            else:
                result["sample"] = json.dumps(payload)[:200]
    except CreditsExhausted as exc:
        result["status"] = "credits_exhausted"
        result["message"] = str(exc)[:200]
    except PappersToolError as exc:
        result["status"] = "tool_error"
        result["message"] = str(exc)[:200]
    except Exception as exc:  # noqa: BLE001
        result["status"] = "exception"
        result["error_class"] = type(exc).__name__
        result["message"] = str(exc)[:200]

    bal_after = get_balance()
    payg_after = bal_after["jetons_pay_as_you_go_restants"]
    abo_after = bal_after["jetons_abonnement"] - bal_after["jetons_abonnement_utilises"]
    result["payg_after"] = payg_after
    result["abo_after"] = abo_after
    result["delta_payg"] = payg_before - payg_after
    result["delta_abo"] = abo_before - abo_after

    return result


async def main() -> None:
    bal_start = get_balance()
    print("=== Solde initial ===")
    print(json.dumps(bal_start, indent=2, ensure_ascii=False))
    print()

    results: list[dict] = []
    for name, args in PROBES:
        print(f"--- Probing {name} ---")
        r = await probe_one(name, args)
        results.append(r)
        print(
            f"  status={r['status']:18s} "
            f"delta_payg={r['delta_payg']:+d} "
            f"delta_abo={r['delta_abo']:+d}"
        )
        if r["status"] in ("business_error", "credits_exhausted"):
            print(f"  message: {r.get('message', '')[:160]}")
        elif r["status"] == "ok":
            print(f"  sample: {r.get('sample', '')[:120]!r}")
        else:
            print(f"  msg: {r.get('message', '')[:160]}")
        print()

    print("=== Matrice de compatibilité PAYG ===")
    print(f"{'tool':<35} {'status':<20} {'cost_payg':>10} {'cost_abo':>10}")
    print("-" * 80)
    for r in results:
        cost_p = f"+{r['delta_payg']}" if r["delta_payg"] > 0 else "0"
        cost_a = f"+{r['delta_abo']}" if r["delta_abo"] > 0 else "0"
        print(f"{r['tool']:<35} {r['status']:<20} {cost_p:>10} {cost_a:>10}")

    bal_end = get_balance()
    print()
    print("=== Solde final ===")
    print(json.dumps(bal_end, indent=2, ensure_ascii=False))
    total_consumed_payg = (
        bal_start["jetons_pay_as_you_go_restants"] - bal_end["jetons_pay_as_you_go_restants"]
    )
    print(f"\nTotal PAYG consommés sur ce probe : {total_consumed_payg}")

    out = ROOT / "traces" / "S095_payg_probe_matrix.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"\nMatrice sauvegardée : {out}")


if __name__ == "__main__":
    asyncio.run(main())
