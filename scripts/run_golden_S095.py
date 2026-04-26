"""Runner unifié pour le pack golden G2-G5 (live, partage cache process).

Pourquoi un script et pas ``pytest tests/integration/test_S095_golden_prompts.py`` :
le conftest pytest a une fixture ``_fresh_cache`` autouse qui réinitialise
``mcp_pappers.cache`` entre **chaque** test. C'est correct pour
l'isolation unitaire, mais ça force à re-payer les appels Pappers
identiques (ex: ``sirenisateur(LVMH)`` re-payé entre G4 et G5).

Ce script exécute G2-G5 **dans un seul process Python** sans fixture
pytest → le cache 24 h ``ToolCache`` opère en RAM, et on minimise la
consommation crédits comme demandé par la story §"Politique d'économie
crédits Pappers".

Usage::

    uv run python scripts/run_golden_S095.py
    uv run python scripts/run_golden_S095.py --output-dir traces/

Sortie : un résumé Markdown sur stdout + 1 JSONL events par prompt
dans ``--output-dir`` (défaut ``traces/``). Code retour 0 si **tous**
les golden passent (no truncation marker + assertion métier OK).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Load .env BEFORE importing the agent (settings are read at import).
load_dotenv(ROOT / ".env")
# Bump wall-clock cap pour couvrir les latences cumulées Sonnet +
# Pappers + lookups vault (cf. pattern test_S08_u3_live.py — WSL +
# Anthropic global = ~3-5 s par round-trip cumulé). Lu à module-load
# par caps.py via WALL_CLOCK_S_OVERRIDE.
os.environ.setdefault("WALL_CLOCK_S_OVERRIDE", "180")

from genial_agent.agent import ConversationState  # noqa: E402
from genial_agent.guardrails.pipeline import run_guarded_turn  # noqa: E402
from genial_agent.observability import stats as stats_mod  # noqa: E402

GoldenAssertion = Callable[[str], tuple[bool, str]]

TRUNCATION_MARKERS = (
    "données tronquées",
    "donnees tronquees",
    "données coupées",
    "réponse tronquée",
    "extrait tronqué",
    "tronqué",
    "tronque",
    "tronquée",
    "non directement lisible",
)


def _no_truncation_marker(text: str) -> tuple[bool, str]:
    lower = text.lower()
    for marker in TRUNCATION_MARKERS:
        if marker in lower:
            return (False, f"marker présent : {marker!r}")
    return (True, "OK")


def _has_recent_ca(text: str) -> tuple[bool, str]:
    lower = text.lower()
    has_year = any(year in lower for year in ("2022", "2023", "2024"))
    has_amount = bool(re.search(r"\d", text)) and any(
        m in lower
        for m in (
            "md€",
            "m€",
            "milliards",
            "millions",
            "eur",
            "€",
            "chiffre d'affaires",
            "ca ",
        )
    )
    if not has_year:
        return (False, "aucune année récente (2022-2024) citée")
    if not has_amount:
        return (False, "aucun chiffre/marqueur monétaire trouvé")
    return (True, "OK")


def _has_at_least_n_sirens(n: int) -> Callable[[str], tuple[bool, str]]:
    def _check(text: str) -> tuple[bool, str]:
        # Le system prompt incite Claude à formater les SIREN en groupes
        # de 3 chiffres pour lisibilité ("775 670 417"). On collapse
        # uniquement les espaces ENTRE digits (lookbehind/lookahead) pour
        # préserver les word boundaries autour du nombre.
        normalized = re.sub(r"(?<=\d)\s+(?=\d)", "", text)
        sirens = set(re.findall(r"\b\d{9}\b", normalized))
        if len(sirens) >= n:
            return (True, f"{len(sirens)} SIRENs distincts cités")
        return (False, f"{len(sirens)} SIRENs distincts seulement (cible {n})")

    return _check


def _has_3y_compare(text: str) -> tuple[bool, str]:
    lower = text.lower()
    years = {y for y in ("2021", "2022", "2023", "2024") if y in lower}
    if len(years) < 3:
        return (False, f"{len(years)} années trouvées (cible 3)")
    digits = re.findall(r"\d[\d\s,.]{2,}", text)
    if len(digits) < 6:
        return (False, f"{len(digits)} chiffres trouvés (cible ≥ 6)")
    return (True, f"{len(years)} années + {len(digits)} chiffres datés")


def _has_resultat_net_2023(text: str) -> tuple[bool, str]:
    lower = text.lower()
    has_metric = any(m in lower for m in ("résultat net", "resultat net", "bénéfice", "benefice"))
    has_year = "2023" in lower
    if not has_metric:
        return (False, "pas de mention 'résultat net' / 'bénéfice'")
    if not has_year:
        return (False, "pas de mention de l'année 2023")
    return (True, "OK")


# Pack G2-G5 (G1 déjà validé en Step 1).
GOLDEN_PROMPTS: list[tuple[str, str, GoldenAssertion]] = [
    ("G2", "Quels sont les mandats de Bernard Arnault ?", _has_at_least_n_sirens(10)),
    (
        "G3",
        "Compare la santé financière de Carrefour vs Casino sur 3 ans",
        _has_3y_compare,
    ),
    ("G4", "Quel est le résultat net de LVMH 2023 ?", _has_resultat_net_2023),
    ("G5", "Liste les filiales de LVMH", _has_at_least_n_sirens(15)),
]


@dataclass
class RunSummary:
    label: str
    prompt: str
    latency_ms: int = 0
    final_text: str = ""
    final_text_len: int = 0
    model_used: str | None = None
    end_reason: str | None = None
    critic_color: str | None = None
    tool_calls_count: int = 0
    local_lookup_count: int = 0
    pappers_calls_consumed: int = 0
    tool_uses: list[dict] = field(default_factory=list)
    payload_offloaded: list[dict] = field(default_factory=list)
    payload_inspected: list[dict] = field(default_factory=list)
    payload_searched: list[dict] = field(default_factory=list)
    capped: list[dict] = field(default_factory=list)
    no_trunc_ok: bool = False
    no_trunc_reason: str = ""
    assertion_ok: bool = False
    assertion_reason: str = ""

    @property
    def passed(self) -> bool:
        return self.no_trunc_ok and self.assertion_ok


async def _run_one(
    label: str,
    prompt: str,
    assertion: GoldenAssertion,
    output_dir: Path | None,
) -> RunSummary:
    summary = RunSummary(label=label, prompt=prompt)
    state = ConversationState()
    started = time.monotonic()
    pappers_before = stats_mod.pappers_calls_today()

    text_chunks: list[str] = []
    events: list[dict[str, Any]] = []

    async for ev in run_guarded_turn(state, prompt, f"golden_{label.lower()}"):
        events.append(ev)
        etype = ev.get("type")
        if etype == "text":
            text_chunks.append(ev.get("content", ""))
        elif etype == "tool_use":
            summary.tool_uses.append({"name": ev.get("name"), "input": ev.get("input")})
        elif etype == "payload_offloaded":
            summary.payload_offloaded.append(
                {
                    "payload_id": ev.get("payload_id"),
                    "tool_name": ev.get("tool_name"),
                    "size_chars": ev.get("size_chars"),
                }
            )
        elif etype == "payload_inspected":
            summary.payload_inspected.append(
                {
                    "json_path": ev.get("json_path"),
                    "returned_chars": ev.get("returned_chars"),
                }
            )
        elif etype == "payload_searched":
            summary.payload_searched.append(
                {
                    "pattern": ev.get("pattern"),
                    "match_count": ev.get("match_count"),
                }
            )
        elif etype == "capped":
            summary.capped.append({k: ev[k] for k in ("reason_code", "reason") if k in ev})
        elif etype == "end":
            summary.end_reason = ev.get("reason")
        elif etype == "routing_done":
            summary.model_used = ev.get("model_used")
        elif etype == "critic_result":
            summary.critic_color = ev.get("color")

    summary.final_text = "".join(text_chunks)
    summary.final_text_len = len(summary.final_text)
    summary.latency_ms = int((time.monotonic() - started) * 1000)
    summary.tool_calls_count = state.tool_calls_count
    summary.local_lookup_count = state.local_lookup_count
    summary.pappers_calls_consumed = stats_mod.pappers_calls_today() - pappers_before

    summary.no_trunc_ok, summary.no_trunc_reason = _no_truncation_marker(summary.final_text)
    summary.assertion_ok, summary.assertion_reason = assertion(summary.final_text)

    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
        with (output_dir / f"S095_{label.lower()}_iter01.jsonl").open("w") as f:
            for ev in events:
                f.write(json.dumps(ev, default=str) + "\n")

    return summary


def _print_summary(summary: RunSummary) -> None:
    status = "✅ PASS" if summary.passed else "❌ FAIL"
    print(f"\n{'=' * 70}")
    print(f"{status} — {summary.label} : {summary.prompt}")
    print(f"{'=' * 70}")
    print(f"  model_used        : {summary.model_used}")
    print(f"  latency_ms        : {summary.latency_ms}")
    print(f"  end_reason        : {summary.end_reason}")
    print(f"  critic_color      : {summary.critic_color}")
    print(f"  tool_calls (Pappers): {summary.tool_calls_count}")
    print(f"  local_lookups       : {summary.local_lookup_count}")
    print(f"  pappers_credits     : {summary.pappers_calls_consumed}")
    print(f"  tool_uses ({len(summary.tool_uses)}) :")
    for tu in summary.tool_uses:
        print(f"      → {tu['name']} {json.dumps(tu['input'], ensure_ascii=False)[:100]}")
    if summary.payload_offloaded:
        print(f"  payload_offloaded ({len(summary.payload_offloaded)}) :")
        for o in summary.payload_offloaded:
            print(f"      → {o['tool_name']} : {o['size_chars']} chars → {o['payload_id']}")
    if summary.payload_inspected:
        print(f"  payload_inspected ({len(summary.payload_inspected)}) :")
        for i in summary.payload_inspected:
            print(f"      → path='{i['json_path']}' returned={i['returned_chars']} chars")
    if summary.payload_searched:
        print(f"  payload_searched ({len(summary.payload_searched)}) :")
        for s in summary.payload_searched:
            print(f"      → /{s['pattern']}/ → {s['match_count']} matches")
    if summary.capped:
        print(f"  capped : {summary.capped}")
    print(f"  no_trunc_check    : {summary.no_trunc_ok} ({summary.no_trunc_reason})")
    print(f"  assertion_check   : {summary.assertion_ok} ({summary.assertion_reason})")
    print(f"\n  --- Final text (first 500 chars) ---\n  {summary.final_text[:500]!r}")


async def main_async(output_dir: Path | None) -> int:
    pappers_at_start = stats_mod.pappers_calls_today()
    summaries: list[RunSummary] = []

    for label, prompt, assertion in GOLDEN_PROMPTS:
        try:
            summary = await _run_one(label, prompt, assertion, output_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"\n❌ {label} CRASHED : {type(exc).__name__}: {exc}", file=sys.stderr)
            raise
        summaries.append(summary)
        _print_summary(summary)

    pappers_total = stats_mod.pappers_calls_today() - pappers_at_start
    passed = sum(1 for s in summaries if s.passed)
    total = len(summaries)

    print(f"\n{'=' * 70}")
    print(f"BILAN — Golden pack G2-G5 : {passed}/{total} passent")
    print(f"  Crédits Pappers consommés (cumulés sur ce run) : {pappers_total}")
    print(f"{'=' * 70}")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "traces",
        help="Répertoire pour les JSONL events (défaut: traces/).",
    )
    args = parser.parse_args()
    return asyncio.run(main_async(args.output_dir))


if __name__ == "__main__":
    sys.exit(main())
