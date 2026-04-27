"""Tests S10 — voice/mount : flag-gated + idempotent + no-op si désactivé."""

from __future__ import annotations

import dataclasses

import pytest
from starlette.routing import Route

from genial_agent.voice import mount as mount_module


def _set_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    """Remplace ``settings`` (frozen dataclass) sur le module ``mount``."""
    new = dataclasses.replace(mount_module.settings, **overrides)
    monkeypatch.setattr(mount_module, "settings", new)


@pytest.fixture(autouse=True)
def _reset() -> None:
    mount_module.reset_for_tests()
    # Purge toute route éventuelle laissée par un test précédent — la
    # ``chainlit.server.app`` est un singleton process-wide.
    try:
        from chainlit.server import app as cl_app

        mount_module._purge_existing_routes(cl_app.router.routes)
    except Exception:  # noqa: BLE001 — best-effort
        pass
    yield
    mount_module.reset_for_tests()
    try:
        from chainlit.server import app as cl_app

        mount_module._purge_existing_routes(cl_app.router.routes)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _route_paths() -> list[str]:
    from chainlit.server import app as cl_app

    return [getattr(r, "path", None) for r in cl_app.router.routes if isinstance(r, Route)]


def test_no_op_when_voice_mode_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=False)
    # Purge any prior mount from earlier tests.
    from chainlit.server import app as cl_app

    mount_module._purge_existing_routes(cl_app.router.routes)

    mount_module.mount_voice_routes()
    paths = _route_paths()
    assert "/v1/chat/completions" not in paths
    assert "/voice-meta.html" not in paths


def test_mounts_when_voice_mode_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=True)
    mount_module.mount_voice_routes()
    paths = _route_paths()
    assert "/v1/chat/completions" in paths
    assert "/voice-meta.html" in paths


def test_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=True)
    mount_module.mount_voice_routes()
    mount_module.mount_voice_routes()
    mount_module.mount_voice_routes()

    from chainlit.server import app as cl_app

    chat_completion_count = sum(
        1
        for r in cl_app.router.routes
        if isinstance(r, Route) and getattr(r, "path", None) == "/v1/chat/completions"
    )
    voice_meta_count = sum(
        1
        for r in cl_app.router.routes
        if isinstance(r, Route) and getattr(r, "path", None) == "/voice-meta.html"
    )
    assert chat_completion_count == 1
    assert voice_meta_count == 1


def test_purge_removes_owned_paths_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=True)
    mount_module.mount_voice_routes()
    from chainlit.server import app as cl_app

    routes = cl_app.router.routes
    initial_count = len(routes)
    mount_module._purge_existing_routes(routes)
    new_paths = _route_paths()
    assert "/v1/chat/completions" not in new_paths
    assert "/voice-meta.html" not in new_paths
    # On a supprimé exactement 2 routes propriétaires.
    assert len(routes) == initial_count - 2


def test_voice_meta_html_response_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=True, ELEVEN_AGENT_ID="agent_123abc")
    mount_module.mount_voice_routes()

    from chainlit.server import app as cl_app
    from starlette.testclient import TestClient

    client = TestClient(cl_app)
    resp = client.get("/voice-meta.html")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert 'name="genial-voice-mode"' in body
    assert 'content="true"' in body
    assert 'data-agent-id="agent_123abc"' in body


def test_voice_meta_escapes_html_special_chars(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_settings(
        monkeypatch,
        ENABLE_VOICE_MODE=True,
        ELEVEN_AGENT_ID='evil"<script>alert(1)</script>',
    )
    mount_module.mount_voice_routes()

    from chainlit.server import app as cl_app
    from starlette.testclient import TestClient

    client = TestClient(cl_app)
    resp = client.get("/voice-meta.html")
    body = resp.text
    assert "<script>" not in body
    assert "&quot;" in body or "&lt;" in body


def test_chat_completions_endpoint_404_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Couche de défense supplémentaire : si le flag est faux, la route
    n'est même pas montée → POST renvoie 404."""
    _set_settings(monkeypatch, ENABLE_VOICE_MODE=False)
    # S'assurer qu'aucune route n'est laissée en place par un autre test.
    from chainlit.server import app as cl_app

    mount_module._purge_existing_routes(cl_app.router.routes)
    mount_module.mount_voice_routes()

    from starlette.testclient import TestClient

    client = TestClient(cl_app)
    resp = client.post("/v1/chat/completions", json={"messages": [], "stream": True})
    # 404 OU 405 : selon que le catch-all Chainlit (``/{full_path:path}``,
    # GET only) intercepte la requête et renvoie 405 Method Not Allowed,
    # ou qu'aucune route ne match et Starlette renvoie 404. Les deux
    # signalent l'absence de notre endpoint custom — ce qu'on veut.
    assert resp.status_code in (404, 405)
