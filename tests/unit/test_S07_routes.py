"""Tests des endpoints HTTP /health et /stats (S07 §"Test runner").

Le ``mount_routes()`` est idempotent et prepend les routes dans
``chainlit.server.app.router.routes`` ; ``TestClient(cl_app)`` les
hit avant le catch-all Chainlit.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from genial_agent.observability.mount import mount_routes


@pytest.fixture(autouse=True)
def _ensure_mounted() -> Iterator[None]:
    # ``mount_routes`` est idempotent : appeler plusieurs fois est sûr.
    # On NE reset PAS le flag pour ne pas réintroduire les mêmes routes
    # à chaque test (Starlette accepte les doublons mais le 1er match
    # gagne — soit notre handler).
    mount_routes()
    yield


@pytest.fixture
def client() -> TestClient:
    from chainlit.server import app as cl_app

    return TestClient(cl_app)


def test_health_returns_4_keys_when_mcp_ok(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_health() -> dict[str, object]:
        return {"status": "ok", "latency_ms": 12, "tools_count": 7, "error": None}

    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert set(body.keys()) >= {"status", "mcp", "version", "uptime_s"}
    assert body["mcp"]["tools_count"] == 7
    assert body["mcp"]["latency_ms"] == 12


def test_health_status_ko_when_mcp_ko_but_http_200(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UptimeRobot ne doit pas page sur indispo MCP transient — le code
    HTTP reste 200, le statut "ko" est dans le body (cf. S07 phase 1
    §"/health contrat durci")."""

    async def fake_health() -> dict[str, object]:
        return {
            "status": "ko",
            "latency_ms": 3000,
            "tools_count": 0,
            "error": "TimeoutError",
        }

    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ko"
    assert body["mcp"]["error"] == "TimeoutError"


def test_health_overrides_chainlit_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vérifie que c'est BIEN notre handler qui répond (et pas le
    statique Chainlit qui renverrait juste ``{'status': 'ok'}`` sans
    ``mcp``)."""

    async def fake_health() -> dict[str, object]:
        return {"status": "ok", "latency_ms": 1, "tools_count": 1, "error": None}

    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    body = client.get("/health").json()
    assert "mcp" in body  # absent du handler natif Chainlit
    assert "version" in body
    assert "uptime_s" in body


def test_stats_returns_counters(client: TestClient) -> None:
    r = client.get("/stats")
    assert r.status_code == 200
    body = r.json()
    assert {
        "total_turns",
        "total_tool_calls",
        "pappers_calls_today",
        "anthropic_input_tokens",
        "anthropic_output_tokens",
        "errors",
        "started_at",
        "uptime_s",
    } <= set(body.keys())


def test_stats_token_protection(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STATS_TOKEN", "secret-xyz")

    r_no_auth = client.get("/stats")
    assert r_no_auth.status_code == 401
    assert r_no_auth.json() == {"error": "unauthorized"}

    r_bad_scheme = client.get("/stats", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r_bad_scheme.status_code == 401

    r_bad_token = client.get("/stats", headers={"Authorization": "Bearer wrong"})
    assert r_bad_token.status_code == 401

    r_ok = client.get("/stats", headers={"Authorization": "Bearer secret-xyz"})
    assert r_ok.status_code == 200
    assert "total_turns" in r_ok.json()


def test_stats_open_when_no_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sans ``STATS_TOKEN`` env var → endpoint ouvert (choix MVP)."""
    monkeypatch.delenv("STATS_TOKEN", raising=False)
    r = client.get("/stats")
    assert r.status_code == 200


def test_stats_reflects_increments(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STATS_TOKEN", raising=False)
    from genial_agent.observability import stats as s

    s.incr(total_turns=3, total_tool_calls=12, pappers_calls_today=12)
    body = client.get("/stats").json()
    assert body["total_turns"] == 3
    assert body["total_tool_calls"] == 12
    assert body["pappers_calls_today"] == 12
