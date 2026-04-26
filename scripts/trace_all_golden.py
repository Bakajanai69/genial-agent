"""S09.6 — Trace les 5 golden post-implémentation pour debrief utilisateur.

Wall-clock bumpé à 180s. Session_id distinct par prompt pour éviter
la saturation du token_budget (singleton par session). Chaque trace
écrite sous ``traces/S096_<label>_postimpl.jsonl``.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

os.environ["WALL_CLOCK_S"] = "180"

import genial_agent.routing as routing_mod  # noqa: E402

routing_mod.WALL_CLOCK_S = 180

from genial_agent.agent import ConversationState  # noqa: E402
from genial_agent.guardrails.pipeline import run_guarded_turn  # noqa: E402
from genial_agent.observability import stats as stats_mod  # noqa: E402

GOLDEN = [
    ("G1", "Quel est le dernier chiffre d'affaires de Carrefour ?"),
    ("G2", "Quels sont les mandats de Bernard Arnault ?"),
    ("G3", "Compare la santé financière de Carrefour vs Casino sur 3 ans"),
    ("G5", "Liste les filiales de LVMH"),
]


async def trace_one(label: str, prompt: str) -> dict[str, Any]:
    state = ConversationState()
    started = time.monotonic()
    pappers_before = stats_mod.pappers_calls_today()

    text_chunks: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    payload_inspected = []
    payload_searched = []
    payload_offloaded = []
    capped = []
    critic_color = None
    model_used = None
    end_reason = None
    events: list[dict[str, Any]] = []

    async for ev in run_guarded_turn(state, prompt, f"trace_S096_{label}"):
        events.append(ev)
        etype = ev.get("type")
        if etype == "text":
            text_chunks.append(ev.get("content", ""))
        elif etype == "tool_use":
            tool_uses.append({"name": ev.get("name"), "input": ev.get("input")})
        elif etype == "payload_offloaded":
            payload_offloaded.append(
                {"tool_name": ev.get("tool_name"), "size_chars": ev.get("size_chars")}
            )
        elif etype == "payload_inspected":
            payload_inspected.append(
                {"json_path": ev.get("json_path"), "returned_chars": ev.get("returned_chars")}
            )
        elif etype == "payload_searched":
            payload_searched.append(
                {"pattern": ev.get("pattern"), "match_count": ev.get("match_count")}
            )
        elif etype == "capped":
            capped.append({k: ev[k] for k in ("reason_code", "reason") if k in ev})
        elif etype == "end":
            end_reason = ev.get("reason")
        elif etype == "routing_done":
            model_used = ev.get("model_used")
        elif etype == "critic_result":
            critic_color = ev.get("color")

    out = ROOT / "traces" / f"S096_{label}_postimpl.jsonl"
    with out.open("w") as f:
        for ev in events:
            f.write(json.dumps(ev, default=str) + "\n")

    return {
        "label": label,
        "prompt": prompt,
        "model_used": model_used,
        "end_reason": end_reason,
        "critic_color": critic_color,
        "tool_calls_count": state.tool_calls_count,
        "tool_uses": tool_uses,
        "payload_offloaded": payload_offloaded,
        "payload_inspected": payload_inspected,
        "payload_searched": payload_searched,
        "capped": capped,
        "pappers_calls_consumed": stats_mod.pappers_calls_today() - pappers_before,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "final_text": "".join(text_chunks),
    }


async def main() -> None:
    for label, prompt in GOLDEN:
        print(f"\n=== {label} : {prompt} ===")
        try:
            s = await trace_one(label, prompt)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ exception: {type(exc).__name__}: {exc}")
            continue
        print(
            f"  model: {s['model_used']}, tool_calls: {s['tool_calls_count']}, "
            f"PAYG: {s['pappers_calls_consumed']}, latency: {s['latency_ms']}ms, "
            f"critic: {s['critic_color']}"
        )
        print(f"  capped: {s['capped']}")
        print(f"  tool_uses: {[t['name'] for t in s['tool_uses']]}")
        print(
            f"  payload_inspected: {len(s['payload_inspected'])}, "
            f"payload_searched: {len(s['payload_searched'])}"
        )
        print(f"  final_text ({len(s['final_text'])} chars):")
        print("  " + "—" * 70)
        for line in s["final_text"].split("\n"):
            print(f"  {line}")
        print("  " + "—" * 70)


if __name__ == "__main__":
    asyncio.run(main())
