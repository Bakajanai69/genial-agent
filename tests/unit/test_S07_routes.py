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


@pytest.fixture(autouse=True)
def _scrub_routes_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Garantie déterministe :

    - Aucun marqueur Railway runtime n'est posé pendant les tests par
      défaut. Sinon, le ``stats`` handler bascule en mode "prod sans
      STATS_TOKEN → 503" (review S08 §B2) et casse silencieusement les
      tests qui supposent le mode dev.
    - ``STATS_TOKEN`` est explicitement supprimé. Sinon, depuis que la
      var est dans le ``.env`` local du dev (cf. déploiement Railway
      §B2 du 2026-04-25), python-dotenv la charge à l'import et tous
      les tests qui supposent /stats ouvert tombent en 401.

    Les tests qui veulent simuler une config prod re-posent ces vars
    explicitement via ``monkeypatch.setenv`` après ce scrub.
    """
    for name in (
        "RAILWAY_DEPLOYMENT_ID",
        "RAILWAY_REPLICA_ID",
        "RAILWAY_SERVICE_NAME",
        "RAILWAY_PROJECT_NAME",
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_PRIVATE_DOMAIN",
        "STATS_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


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


# ---------------------------------------------------------------------------
# Régressions B3 / M3 / M4 / N3 (review S07)
# ---------------------------------------------------------------------------


def test_stats_token_uses_hmac_compare_digest() -> None:
    """Régression B3 : la comparaison du token doit être timing-safe.

    On valide par inspection de la source pour ne pas dépendre de
    l'observation de timing différentiel (bruité par l'OS scheduler).
    Garde-fou contre une régression silencieuse vers ``==``.
    """
    import inspect

    from genial_agent.observability import routes as routes_mod

    src = inspect.getsource(routes_mod)
    assert "hmac.compare_digest" in src, (
        "routes.py doit utiliser hmac.compare_digest pour comparer le STATS_TOKEN (cf. review B3)"
    )


def test_health_supports_head_method(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression N3 : UptimeRobot peut faire HEAD pour économiser
    la bande passante. Sans ``HEAD`` dans ``methods``, Starlette
    répond 405.
    """

    async def fake_health() -> dict[str, object]:
        return {"status": "ok", "latency_ms": 5, "tools_count": 1, "error": None}

    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", fake_health)
    r = client.head("/health")
    assert r.status_code == 200, f"HEAD /health doit répondre 200, pas {r.status_code}"


def test_health_times_out_when_mcp_hangs(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression M3 : un MCP coincé ne doit pas tenir UptimeRobot
    ouvert 15 s (timeout réseau de ``mcp_pappers``). On force un
    healthcheck infiniment lent et on vérifie que la route répond
    rapidement avec ``status="ko"`` cohérent."""
    import asyncio
    import time

    from genial_agent.observability import routes as routes_mod

    # Timeout court pour le test.
    monkeypatch.setattr(routes_mod, "_HEALTH_MCP_TIMEOUT_S", 0.05)

    async def hanging_health() -> dict[str, object]:
        await asyncio.sleep(2.0)  # bien plus que le timeout
        return {"status": "ok", "latency_ms": 0, "tools_count": 0, "error": None}

    monkeypatch.setattr("genial_agent.mcp_pappers.healthcheck", hanging_health)

    started = time.monotonic()
    r = client.get("/health")
    elapsed = time.monotonic() - started

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ko"
    assert body["mcp"]["error"] == "TimeoutError"
    # Marge généreuse pour ne pas flaker selon la charge CI.
    assert elapsed < 1.0, (
        f"/health a mis {elapsed:.2f}s alors que le timeout est 0.05s → régression M3"
    )


def test_stats_503_in_prod_railway_without_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression S08 §B2 : sur Railway, ``/stats`` doit refuser de servir
    si ``STATS_TOKEN`` n'est pas configuré. Sinon volumétrie + coût +
    crédits Pappers résiduels fuitent sur l'URL publique.
    """
    monkeypatch.delenv("STATS_TOKEN", raising=False)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep_xxx_runtime_only")

    r = client.get("/stats")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "stats_token_required_in_production"
    # Pas de fuite de compteurs dans la réponse d'erreur.
    assert "total_turns" not in body
    assert "anthropic_input_tokens" not in body


def test_stats_serves_in_prod_railway_with_valid_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression S08 §B2 : sur Railway avec ``STATS_TOKEN`` configuré,
    le Bearer correct laisse passer."""
    monkeypatch.setenv("STATS_TOKEN", "prod-secret-xyz")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

    r_no_auth = client.get("/stats")
    assert r_no_auth.status_code == 401  # token configuré → 401, pas 503

    r_ok = client.get("/stats", headers={"Authorization": "Bearer prod-secret-xyz"})
    assert r_ok.status_code == 200
    assert "total_turns" in r_ok.json()


def test_stats_503_triggers_on_any_runtime_marker(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression S08 §B2 : la détection prod doit s'activer sur n'importe
    lequel des marqueurs **runtime-only** Railway. Les vars ``_ID`` que
    les devs stockent dans .env (PROJECT_ID, SERVICE_ID, etc.) ne
    doivent PAS suffire — sinon faux-positif sur tout poste dev."""
    monkeypatch.delenv("STATS_TOKEN", raising=False)

    runtime_markers = (
        "RAILWAY_DEPLOYMENT_ID",
        "RAILWAY_REPLICA_ID",
        "RAILWAY_SERVICE_NAME",
        "RAILWAY_PROJECT_NAME",
        "RAILWAY_ENVIRONMENT_NAME",
        "RAILWAY_PRIVATE_DOMAIN",
    )
    for marker in runtime_markers:
        for other in runtime_markers:
            monkeypatch.delenv(other, raising=False)
        monkeypatch.setenv(marker, "any-value")
        r = client.get("/stats")
        assert r.status_code == 503, f"Marker runtime {marker} doit déclencher le refus"


def test_stats_dev_id_vars_do_not_trigger_prod_mode(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Régression S08 §B2 : les vars ``_ID`` que les devs stockent en
    ``.env`` pour requêter la GraphQL API Railway ne doivent PAS faire
    basculer ``/stats`` en mode prod (sinon impossible de tester /stats
    en local sans STATS_TOKEN). On simule un poste dev qui a tout son
    .env Railway et on vérifie que /stats reste ouvert."""
    monkeypatch.delenv("STATS_TOKEN", raising=False)
    # Vars typiques qu'un dev stocke pour railway.com/account API :
    monkeypatch.setenv("RAILWAY_PROJECT_ID", "uuid-project")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "uuid-service")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_ID", "uuid-env")
    monkeypatch.setenv("RAILWAY_API_TOKEN", "dev-account-token")
    monkeypatch.setenv("RAILWAY_PUBLIC_DOMAIN", "genial-agent-production.up.railway.app")

    r = client.get("/stats")
    assert r.status_code == 200, (
        "Les vars ID/TOKEN/PUBLIC_DOMAIN ne doivent pas faire basculer en mode prod — "
        "elles sont fréquemment dans .env dev pour requêter la GraphQL API Railway."
    )


def test_mount_routes_purges_duplicates_on_remount() -> None:
    """Régression M4 : un cycle ``reset_for_tests`` + ``mount_routes``
    ne doit PAS accumuler de doublons dans ``cl_app.router.routes``.
    """
    from chainlit.server import app as cl_app
    from starlette.routing import Route as _Route

    from genial_agent.observability.mount import (
        mount_routes,
        reset_for_tests,
    )

    # Plusieurs cycles de remount.
    for _ in range(5):
        reset_for_tests()
        mount_routes()

    health_routes = [
        r for r in cl_app.router.routes if isinstance(r, _Route) and r.path == "/health"
    ]
    stats_routes = [r for r in cl_app.router.routes if isinstance(r, _Route) and r.path == "/stats"]
    assert len(health_routes) == 1, (
        f"Accumulation détectée : {len(health_routes)} routes /health (régression M4)"
    )
    assert len(stats_routes) == 1, (
        f"Accumulation détectée : {len(stats_routes)} routes /stats (régression M4)"
    )
