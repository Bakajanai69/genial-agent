"""S09.6 (A1) — Probe matrice tools/retours.

Appelle 1 fois chaque tool retenu sur LVMH (SIREN 775670417), capture
le payload brut et émet une matrice Markdown qui sera collée dans
``docs/pappers-mcp.md`` § "Matrice tools fonctionnels (S09.6)".

Coût attendu : ~6 crédits PAYG total (la matrice S09.6 est figée 1 fois
puis lue par l'agent en lecture seule via le system prompt).

Usage::

    uv run python scripts/probe_tools_matrix.py

Output stdout : tableau Markdown + extraits de payload. À recopier
manuellement dans la doc.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

# Probes — 1 appel par tool, tous sur LVMH (775670417) pour
# comparabilité. Args minimaux fonctionnels (validés contre
# inputSchema réel du MCP Pappers, probe S02 2026-04-24).
PROBES: list[tuple[str, str, dict, str]] = [
    (
        "sirenisateur",
        "sirenisateur",
        {"company_name": "LVMH", "country_code": "FR"},
        'Trouve le SIREN de "X" (langue naturelle → identifiant)',
    ),
    (
        "recherche-entreprises",
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
        "Headline financier 1 année (CA, résultat, effectif) — workaround `comptes-entreprise`",
    ),
    (
        "comptes-entreprise",
        "comptes-entreprise",
        {"siren": "775670417", "annee": "2023"},
        "Bilans détaillés N années (peut renvoyer crédits insuffisants — bug PAYG §4.2)",
    ),
    (
        "cartographie-entreprise",
        "cartographie-entreprise",
        {"siren": "775670417"},
        "Filiales / cartographie groupe (payload volumineux, vault offload)",
    ),
    (
        "recherche-dirigeants",
        "recherche-dirigeants",
        {"q": "Bernard Arnault", "par_page": 5},
        "Mandats actifs d'un dirigeant (par_page borne le coût 1-4 crédits)",
    ),
    (
        "conformite-personne-physique",
        "conformite-personne-physique",
        {"nom": "Arnault", "prenom": "Bernard"},
        "KYC personne physique (gratuit, vérification listes)",
    ),
]


async def probe_one(label: str, tool_name: str, args: dict) -> dict:
    """Appelle ``_invoke_tool_live`` (bypass cache) et capture le
    résultat avec son schéma top-level + un extrait du contenu."""
    from genial_agent.mcp_pappers import (
        _extract_business_error,
        _invoke_tool_live,
    )

    out: dict = {"label": label, "tool": tool_name, "args": args}
    try:
        payload = await _invoke_tool_live(tool_name, args)
    except Exception as exc:  # noqa: BLE001
        out["status"] = "exception"
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out

    biz = _extract_business_error(payload)
    if biz is not None:
        out["status"] = "business_error"
        out["error"] = biz[:200]
        return out

    content = payload.get("content")
    if not isinstance(content, list) or not content:
        out["status"] = "empty"
        out["sample"] = ""
        out["payload_size"] = 0
        return out

    first_text = next(
        (b.get("text", "") for b in content if isinstance(b, dict) and "text" in b),
        "",
    )
    out["status"] = "ok"
    out["payload_size"] = len(first_text)

    # Top-level keys : on parse le 1er texte si JSON, sinon on échantillonne.
    try:
        parsed = json.loads(first_text)
    except (ValueError, TypeError):
        out["top_level_keys"] = []
        out["sample"] = first_text[:400]
        return out

    if isinstance(parsed, dict):
        out["top_level_keys"] = list(parsed.keys())
    elif isinstance(parsed, list) and parsed:
        first = parsed[0]
        out["top_level_keys"] = list(first.keys()) if isinstance(first, dict) else []
    else:
        out["top_level_keys"] = []
    out["sample"] = first_text[:400]
    return out


async def main() -> int:
    results: list[dict] = []
    for label, tool_name, args, _ in PROBES:
        print(f"--- Probing {label} ---", file=sys.stderr)
        r = await probe_one(label, tool_name, args)
        results.append(r)
        print(
            f"  status={r['status']} size={r.get('payload_size', '?')} "
            f"top_keys={r.get('top_level_keys', [])[:5]}",
            file=sys.stderr,
        )

    # Matrice Markdown.
    print("\n## Matrice tools fonctionnels (S09.6)\n")
    print(
        "Capture live 2026 — 1 appel/tool sur LVMH (SIREN 775670417). "
        "Reproduction : `uv run python scripts/probe_tools_matrix.py`. "
        "Coût indicatif : ~6 crédits PAYG.\n"
    )
    print(
        "| Tool | Args minimaux | Top-level keys (head 5) "
        "| Taille payload (chars) | Cas d'usage couvert |"
    )
    print("|---|---|---|---:|---|")
    for (label, _, args, usage), r in zip(PROBES, results, strict=True):
        keys = r.get("top_level_keys", [])
        keys_str = ", ".join(f"`{k}`" for k in keys[:5]) if keys else "_(n/a)_"
        size = r.get("payload_size", 0)
        if r["status"] != "ok":
            keys_str = f"⚠ _{r['status']}_"
            size_str = "—"
        else:
            size_str = f"~{size:,}"
        args_str = ", ".join(f"`{k}={v!r}`" for k, v in list(args.items())[:3])
        if len(args) > 3:
            args_str += ", …"
        print(f"| `{label}` | {args_str} | {keys_str} | {size_str} | {usage} |")

    # Extraits de payload.
    print("\n### Extraits (head 400 chars)\n")
    for r in results:
        print(f"#### `{r['label']}`")
        if r["status"] != "ok":
            print(f"⚠ **{r['status']}** : `{r.get('error', '')}`\n")
            continue
        sample = r.get("sample", "")
        print("```json")
        print(sample[:400] + ("…" if len(sample) > 400 else ""))
        print("```\n")

    # Sauvegarde JSON brute pour audit.
    out_path = ROOT / "traces" / "S096_tools_matrix.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"_(matrice JSON brute : `{out_path.relative_to(ROOT)}`)_", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
