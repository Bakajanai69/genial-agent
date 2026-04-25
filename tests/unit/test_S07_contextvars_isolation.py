"""Vérifie que ``structlog.contextvars`` isole bien deux turns concurrents.

``contextvars`` est task-local en asyncio (chaque task a une copie
copy-on-write) — un ``bind_contextvars(session_id=…)`` dans une task
ne contamine pas une autre task. Test crucial : sans cette garantie,
deux conversations en parallèle mêleraient leurs ``session_id`` dans
les logs.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import structlog

from genial_agent.observability.logging import configure_logging, reset_for_tests


async def test_contextvars_isolation_between_concurrent_turns(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reset_for_tests()
    configure_logging()
    structlog.contextvars.clear_contextvars()
    logger = structlog.get_logger("test_S07_iso")

    async def turn(sid: str) -> None:
        with structlog.contextvars.bound_contextvars(session_id=sid):
            await asyncio.sleep(0.01)
            logger.info(f"event_for_{sid}")

    await asyncio.gather(turn("alpha"), turn("beta"))

    out = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(line) for line in out]
    by_msg = {p["msg"]: p for p in parsed if "msg" in p}

    assert "event_for_alpha" in by_msg, f"missing alpha in {list(by_msg)}"
    assert "event_for_beta" in by_msg, f"missing beta in {list(by_msg)}"
    assert by_msg["event_for_alpha"]["session_id"] == "alpha"
    assert by_msg["event_for_beta"]["session_id"] == "beta"


async def test_contextvars_release_after_block(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``bound_contextvars`` est un context manager — la sortie purge
    les binds. Sinon un turn fuite son ``session_id`` aux logs hors-turn."""
    reset_for_tests()
    configure_logging()
    structlog.contextvars.clear_contextvars()
    logger = structlog.get_logger("test_S07_release")

    with structlog.contextvars.bound_contextvars(session_id="zzz"):
        logger.info("inside")
    logger.info("outside")

    out = capsys.readouterr().out.strip().splitlines()
    parsed = [json.loads(line) for line in out]
    by_msg = {p["msg"]: p for p in parsed if "msg" in p}

    assert by_msg["inside"]["session_id"] == "zzz"
    assert "session_id" not in by_msg["outside"]
