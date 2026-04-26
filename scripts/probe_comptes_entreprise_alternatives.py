"""Probe `comptes-entreprise` + alternatives pour contourner le bug PAYG.

Tests successifs (économique : ≤ 7 crédits PAYG total) :

A. Re-test direct ``comptes-entreprise(LVMH, 2023)`` — voir si Pappers
   a fixé le bug entre-temps.
B. ``recherche-entreprises`` avec ``return_fields`` financier
   (``chiffre_affaires``, ``resultat``, ...) — workaround **majeur**
   parce que ce tool accepte le PAYG.
C. ``comptes-entreprise`` sans ``annee`` (peut-être que sans filtre,
   le tool est routé différemment).
D. ``comptes-entreprise`` sur Carrefour Hyper — voir si c'est
   entité-spécifique ou bien systémique.

Reporte la matrice + extrait significatif des payloads pour qu'on
sache si ``recherche-entreprises`` suffit à répondre aux questions
financières U3.
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
    _extract_business_error,
    _invoke_tool_live,
)


def get_balance() -> dict[str, int]:
    key = os.getenv("PAPPERS_API_KEY")
    r = httpx.get(f"https://api.pappers.fr/v2/suivi-jetons?api_token={key}", timeout=15)
    r.raise_for_status()
    return r.json()


PROBES: list[tuple[str, str, dict]] = [
    (
        "A_retest_comptes_lvmh_2023",
        "comptes-entreprise",
        {"siren": "775670417", "annee": "2023"},
    ),
    (
        "B_workaround_recherche_entreprises_lvmh",
        "recherche-entreprises",
        {
            "q": "LVMH",
            "par_page": 1,
            "return_fields": [
                "siren",
                "nom_entreprise",
                "chiffre_affaires",
                "resultat",
                "capital",
                "effectif",
                "annee_finances",
                "annee_effectif",
            ],
        },
    ),
    (
        "C_comptes_lvmh_no_annee",
        "comptes-entreprise",
        {"siren": "775670417"},
    ),
    (
        "D_comptes_carrefour_hyper_2023",
        "comptes-entreprise",
        {"siren": "451321335", "annee": "2023"},
    ),
]


async def probe(label: str, name: str, args: dict) -> dict:
    bal_before = get_balance()
    payg_before = bal_before["jetons_pay_as_you_go_restants"]
    out: dict = {"label": label, "tool": name, "args": args, "payg_before": payg_before}

    try:
        payload = await _invoke_tool_live(name, args)
        biz = _extract_business_error(payload)
        if biz:
            out["status"] = "business_error"
            out["message"] = biz[:300]
        else:
            out["status"] = "ok"
            content = payload.get("content")
            if isinstance(content, list) and content:
                first_text = next((b.get("text", "") for b in content if isinstance(b, dict)), "")
                out["sample"] = first_text[:600]
                out["payload_size"] = len(first_text)
            else:
                out["sample"] = json.dumps(payload)[:600]
    except Exception as exc:  # noqa: BLE001
        out["status"] = "exception"
        out["message"] = f"{type(exc).__name__}: {str(exc)[:200]}"

    bal_after = get_balance()
    out["payg_after"] = bal_after["jetons_pay_as_you_go_restants"]
    out["delta_payg"] = payg_before - out["payg_after"]
    return out


async def main() -> None:
    bal_start = get_balance()
    print("=== Solde initial ===")
    print(json.dumps(bal_start, indent=2, ensure_ascii=False))
    print()

    results = []
    for label, name, args in PROBES:
        print(f"--- {label} ({name}) ---")
        r = await probe(label, name, args)
        results.append(r)
        print(f"  status={r['status']} delta_payg=-{r['delta_payg']}")
        if r["status"] == "ok":
            print(f"  payload_size={r.get('payload_size', '?')} chars")
            print(f"  sample (head 400): {r.get('sample', '')[:400]!r}")
        else:
            print(f"  msg: {r.get('message', '')[:300]}")
        print()

    bal_end = get_balance()
    print("=== Solde final ===")
    print(json.dumps(bal_end, indent=2, ensure_ascii=False))
    print(
        f"\nTotal PAYG consommés : {bal_start['jetons_pay_as_you_go_restants'] - bal_end['jetons_pay_as_you_go_restants']}"
    )

    out_path = ROOT / "traces" / "S095_comptes_alternatives_probe.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))
    print(f"\nMatrice sauvegardée : {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
