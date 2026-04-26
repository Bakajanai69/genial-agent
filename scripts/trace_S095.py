"""Trace fine d'un tour agent — observation S09.5 phase 2.

Capture pour chaque tour :

- Reçu MCP : tool_name, args, raw_size_chars, raw_head_1kb (preview).
- Décision agent : tool_uses[].{name, input}.
- Action exécutée : payload_inspect/search → {json_path / pattern,
  returned_chars, value_head}.
- Réponse finale : final_text, model_used, tool_calls_count,
  local_lookup_count, latency_ms.
- Méta cache : pappers_calls (start/end), payload_offloaded /
  inspected / searched counts.

Usage::

    python scripts/trace_S095.py "Quel est le dernier CA de Carrefour ?"
    python scripts/trace_S095.py "Liste filiales LVMH" \\
        --output traces/S095_lvmh_iter01.jsonl

Cf. docs/stories/S09.5-mcp-payload-handling.md §"🔬 Boucle de
validation observée".
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Load .env BEFORE importing the agent (settings are read at import).
load_dotenv(ROOT / ".env")

from genial_agent.agent import ConversationState  # noqa: E402
from genial_agent.guardrails.pipeline import run_guarded_turn  # noqa: E402
from genial_agent.observability import stats as stats_mod  # noqa: E402


async def trace_turn(prompt: str, output: Path | None = None) -> dict[str, Any]:
    state = ConversationState()
    started = time.monotonic()
    pappers_before = stats_mod.pappers_calls_today()

    events: list[dict[str, Any]] = []
    text_chunks: list[str] = []
    final_text = ""
    model_used = None
    end_reason = None
    tool_uses: list[dict[str, Any]] = []
    payload_offloaded: list[dict[str, Any]] = []
    payload_inspected: list[dict[str, Any]] = []
    payload_searched: list[dict[str, Any]] = []
    capped: list[dict[str, Any]] = []
    critic_color = None

    async for ev in run_guarded_turn(state, prompt, "trace_S095"):
        events.append(ev)
        etype = ev.get("type")
        if etype == "text":
            text_chunks.append(ev.get("content", ""))
        elif etype == "tool_use":
            tool_uses.append({"name": ev.get("name"), "input": ev.get("input")})
        elif etype == "payload_offloaded":
            payload_offloaded.append(
                {
                    "payload_id": ev.get("payload_id"),
                    "tool_name": ev.get("tool_name"),
                    "size_chars": ev.get("size_chars"),
                }
            )
        elif etype == "payload_inspected":
            payload_inspected.append(
                {
                    "payload_id": ev.get("payload_id"),
                    "json_path": ev.get("json_path"),
                    "returned_chars": ev.get("returned_chars"),
                }
            )
        elif etype == "payload_searched":
            payload_searched.append(
                {
                    "payload_id": ev.get("payload_id"),
                    "pattern": ev.get("pattern"),
                    "match_count": ev.get("match_count"),
                }
            )
        elif etype == "capped":
            capped.append({k: ev[k] for k in ("reason_code", "reason") if k in ev})
        elif etype == "end":
            end_reason = ev.get("reason")
        elif etype == "routing_done":
            model_used = ev.get("model_used")
        elif etype == "critic_result":
            critic_color = ev.get("color")

    final_text = "".join(text_chunks)
    latency_ms = int((time.monotonic() - started) * 1000)
    pappers_after = stats_mod.pappers_calls_today()

    summary = {
        "prompt": prompt,
        "latency_ms": latency_ms,
        "model_used": model_used,
        "end_reason": end_reason,
        "critic_color": critic_color,
        "tool_calls_count": state.tool_calls_count,
        "local_lookup_count": state.local_lookup_count,
        "tool_uses": tool_uses,
        "payload_offloaded": payload_offloaded,
        "payload_inspected": payload_inspected,
        "payload_searched": payload_searched,
        "capped": capped,
        "pappers_calls_consumed": pappers_after - pappers_before,
        "vault_size_at_end": len(state.payload_vault),
        "final_text": final_text,
        "final_text_len": len(final_text),
    }

    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w") as f:
            for ev in events:
                f.write(json.dumps(ev, default=str) + "\n")

    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", help="Prompt utilisateur à tracer.")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Fichier JSONL pour les events bruts (optionnel).",
    )
    args = parser.parse_args()

    summary = asyncio.run(trace_turn(args.prompt, args.output))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
