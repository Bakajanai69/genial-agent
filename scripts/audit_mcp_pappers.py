"""Audit complet du serveur MCP Pappers — gratuit (tools/list seulement).

Génère un état des lieux structuré des 31 tools exposés :

- Inventaire complet (nom, title, description, required, props head).
- Catégorisation par domaine (entreprise / juridique / immobilier / etc.).
- Comparaison RETAINED_TOOLS (7 retenus côté agent) vs les exclus.
- Qualité des descriptions (longueur, présence d'exemples, ambiguïtés).
- Gaps potentiels vs les cas d'usage U1-U5 du cahier.

Usage::

    uv run python scripts/audit_mcp_pappers.py > docs/mcp-audit.md

Sortie : Markdown directement écrivable dans docs/.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT / ".env")

from genial_agent.config import settings  # noqa: E402
from genial_agent.mcp_pappers import RETAINED_TOOLS  # noqa: E402

# Catégorisation heuristique par préfixe / mot-clé du nom de tool.
# Permet de regrouper les 31 tools par domaine produit Pappers.
DOMAIN_RULES: list[tuple[str, list[str]]] = [
    (
        "Entreprise (core)",
        [
            "sirenisateur",
            "recherche-entreprises",
            "comptes-entreprise",
            "cartographie-entreprise",
            "recherche-dirigeants",
            "recherche-beneficiaires",
            "informations-entreprise",
            "actes-entreprise",
            "documents-entreprise",
        ],
    ),
    ("Conformité / KYC", ["conformite", "sanctions", "pep", "kyc"]),
    (
        "Justice / Juridique",
        [
            "jurisprudence",
            "decision",
            "arret",
            "recherche-decisions",
            "question-juridique",
            "tribunaux",
        ],
    ),
    ("Annonces légales", ["annonces", "bodacc", "publication"]),
    ("Immobilier", ["immobilier", "dvf", "transactions"]),
    ("Politique / Institutionnel", ["politique", "elu", "mandats-publics", "institutions"]),
    ("Territoire", ["territoire", "commune", "departement", "region"]),
    ("Surveillance", ["surveillance", "alerte", "veille"]),
]


def categorize(name: str) -> str:
    for domain, patterns in DOMAIN_RULES:
        for p in patterns:
            if p in name.lower():
                return domain
    return "Autre / Non catégorisé"


def quality_signals(t: dict[str, Any]) -> list[str]:
    """Heuristiques de qualité de la description du tool."""
    signals = []
    desc = (t.get("description") or "").strip()
    if not desc:
        signals.append("⚠ description vide")
    elif len(desc) < 80:
        signals.append(f"⚠ description courte ({len(desc)} chars)")
    if (
        "exemple" not in desc.lower()
        and "example" not in desc.lower()
        and "ex." not in desc.lower()
        and "ex:" not in desc.lower()
    ):
        signals.append("ℹ pas d'exemple dans la description")
    if not t.get("inputSchema", {}).get("required"):
        signals.append("ℹ aucun paramètre required (peut être ambigu pour le LLM)")
    out_schema = t.get("outputSchema")
    if not out_schema:
        signals.append("ℹ outputSchema absent (l'agent ne sait pas la shape)")
    return signals


async def fetch_all_tools() -> list[dict[str, Any]]:
    url = f"https://mcp.pappers.fr/{settings.PAPPERS_API_KEY}"
    async with (
        create_mcp_http_client(timeout=httpx.Timeout(15, connect=10, read=15)) as http,
        streamable_http_client(url, http_client=http) as (rs, ws, _),
        ClientSession(rs, ws) as session,
    ):
        await session.initialize()
        result = await session.list_tools()
        return [t.model_dump(mode="json") for t in result.tools]


def render_md(tools: list[dict[str, Any]]) -> str:
    """Génère le rapport Markdown."""
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for t in tools:
        by_domain[categorize(t["name"])].append(t)

    lines: list[str] = []
    lines.append("# Audit MCP Pappers — état des lieux")
    lines.append("")
    lines.append(f"> Généré : {datetime.now(UTC).isoformat()}")
    lines.append("> Source : ``mcp.pappers.fr`` via streamable-http, ``tools/list`` (gratuit).")
    lines.append(f"> Total tools exposés : **{len(tools)}**")
    lines.append(f"> Tools retenus côté agent (``RETAINED_TOOLS``) : **{len(RETAINED_TOOLS)}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. Vue d'ensemble par domaine")
    lines.append("")
    lines.append("| Domaine | Total | Retenus | Exclus |")
    lines.append("|---|---:|---:|---:|")
    for domain, ts in sorted(by_domain.items(), key=lambda kv: -len(kv[1])):
        n = len(ts)
        kept = sum(1 for t in ts if t["name"] in RETAINED_TOOLS)
        excluded = n - kept
        lines.append(f"| {domain} | {n} | {kept} | {excluded} |")
    lines.append(
        f"| **Total** | **{len(tools)}** | **{sum(1 for t in tools if t['name'] in RETAINED_TOOLS)}** | "
        f"**{sum(1 for t in tools if t['name'] not in RETAINED_TOOLS)}** |"
    )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Section : Tools retenus (détail riche)
    retained_tools = [t for t in tools if t["name"] in RETAINED_TOOLS]
    lines.append(f"## 2. Tools retenus ({len(retained_tools)}) — détail")
    lines.append("")
    for t in sorted(retained_tools, key=lambda x: x["name"]):
        lines.append(f"### `{t['name']}`")
        lines.append("")
        title = t.get("title") or ""
        if title and title != t["name"]:
            lines.append(f"**Title** : {title}")
            lines.append("")
        desc = (t.get("description") or "").strip()
        lines.append(f"**Description** : {desc[:600]}")
        lines.append("")
        schema = t.get("inputSchema", {})
        req = schema.get("required", [])
        props = list(schema.get("properties", {}).keys())
        lines.append(f"**Required** : `{req}`")
        lines.append(f"**Props ({len(props)}) head 8** : `{props[:8]}`")
        lines.append("")
        signals = quality_signals(t)
        if signals:
            lines.append("**Signaux qualité** :")
            for s in signals:
                lines.append(f"- {s}")
            lines.append("")
        lines.append("")

    # Section : Tools exclus (synthèse)
    lines.append("---")
    lines.append("")
    excluded = [t for t in tools if t["name"] not in RETAINED_TOOLS]
    lines.append(f"## 3. Tools exclus ({len(excluded)}) — synthèse")
    lines.append("")
    lines.append(
        "Ces tools sont exposés par le MCP mais filtrés au niveau "
        "``RETAINED_TOOLS`` côté ``mcp_pappers.py``. Justification : "
        "soit hors scope MVP cahier, soit Premium-only, soit "
        "redondant avec un tool déjà retenu, soit en cours de "
        "spécification côté Pappers."
    )
    lines.append("")
    lines.append("| Nom | Domaine | Required | Description (head 100 chars) |")
    lines.append("|---|---|---|---|")
    for t in sorted(excluded, key=lambda x: (categorize(x["name"]), x["name"])):
        domain = categorize(t["name"])
        req = ", ".join(t.get("inputSchema", {}).get("required", []))
        desc = (t.get("description") or "").replace("\n", " ").replace("|", "\\|")[:100]
        lines.append(f"| `{t['name']}` | {domain} | `{req or '—'}` | {desc} |")
    lines.append("")

    # Section : Gaps vs U1-U5
    lines.append("---")
    lines.append("")
    lines.append("## 4. Couverture des cas d'usage U1-U5 (cahier §3)")
    lines.append("")
    coverage = {
        "U1 fiche identité": ("✅", ["sirenisateur", "recherche-entreprises"]),
        "U2 cartographie dirigeant": (
            "✅",
            ["sirenisateur", "recherche-dirigeants", "cartographie-entreprise"],
        ),
        "U3 comparaison financière 3 ans": (
            "⚠ partiel (workaround `recherche-entreprises`)",
            ["sirenisateur", "comptes-entreprise"],
        ),
        "U4 recherche par critères": ("✅", ["recherche-entreprises"]),
        "U5 KYC / vérification": ("✅", ["sirenisateur", "conformite-personne-physique"]),
    }
    lines.append("| Cas d'usage | Couvert ? | Tools utilisés |")
    lines.append("|---|---|---|")
    for cas, (status, tools_used) in coverage.items():
        lines.append(f"| {cas} | {status} | {', '.join(f'`{t}`' for t in tools_used)} |")
    lines.append("")

    # Section : Quality summary
    lines.append("---")
    lines.append("")
    lines.append("## 5. Qualité des descriptions MCP — vue rapide")
    lines.append("")
    short_desc = [t for t in tools if len(t.get("description") or "") < 80]
    no_required = [t for t in tools if not t.get("inputSchema", {}).get("required")]
    no_output_schema = [t for t in tools if not t.get("outputSchema")]
    lines.append(f"- Tools avec description **< 80 chars** : {len(short_desc)} sur {len(tools)}")
    lines.append(
        f"- Tools **sans `required`** : {len(no_required)} sur {len(tools)} "
        f"(LLM peut chercher des combinaisons floues d'args)"
    )
    lines.append(
        f"- Tools **sans `outputSchema`** : {len(no_output_schema)} sur {len(tools)} "
        f"(l'agent doit deviner la shape de retour)"
    )
    lines.append("")
    if no_output_schema and len(no_output_schema) > len(tools) // 2:
        lines.append(
            "⚠ Plus de la moitié des tools n'ont pas d'outputSchema — "
            "c'est cohérent avec les payloads volumineux et inattendus "
            "qui ont motivé le Payload Vault S09.5 : impossible pour "
            "l'agent de prévoir la structure du retour."
        )
        lines.append("")

    return "\n".join(lines)


async def main() -> int:
    tools = await fetch_all_tools()
    md = render_md(tools)
    print(md)
    # Écrit aussi un dump JSON brut pour reference
    out_json = ROOT / "traces" / "S095_mcp_audit_dump.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(tools, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
