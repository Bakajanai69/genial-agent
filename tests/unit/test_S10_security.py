"""Tests S10 — voice/security : Bearer timing-safe + jamais log la valeur."""

from __future__ import annotations

import dataclasses
import hmac
import json
from unittest.mock import patch

import pytest
from starlette.requests import Request

from genial_agent.voice import security as security_module
from genial_agent.voice.security import verify_eleven_request


def _patch_token(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    """``Settings`` est ``frozen`` → on remplace l'instance entière."""
    new_settings = dataclasses.replace(security_module.settings, ELEVEN_AGENT_SHARED_TOKEN=token)
    monkeypatch.setattr(security_module, "settings", new_settings)


def _make_request(headers: dict[str, str] | None = None) -> Request:
    """Construit une ``starlette.Request`` synthétique pour les tests."""
    raw_headers = []
    for k, v in (headers or {}).items():
        raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/chat/completions",
        "headers": raw_headers,
        "query_string": b"",
    }
    return Request(scope)


@pytest.fixture
def valid_token(monkeypatch: pytest.MonkeyPatch) -> str:
    token = "test-shared-token-32-chars-aaaaaaaaaa"
    # Settings est frozen — on patch l'attribut directement sur l'instance.
    _patch_token(monkeypatch, token)
    return token


def test_no_token_configured_returns_401(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_token(monkeypatch, "")
    req = _make_request({"authorization": "Bearer whatever"})
    response = verify_eleven_request(req)
    assert response is not None
    assert response.status_code == 401


def test_missing_header_returns_401(valid_token: str) -> None:  # noqa: ARG001
    req = _make_request()
    response = verify_eleven_request(req)
    assert response is not None
    assert response.status_code == 401


def test_malformed_header_returns_401(valid_token: str) -> None:  # noqa: ARG001
    req = _make_request({"authorization": "NotBearer foo"})
    response = verify_eleven_request(req)
    assert response is not None
    assert response.status_code == 401


def test_empty_token_returns_401(valid_token: str) -> None:  # noqa: ARG001
    req = _make_request({"authorization": "Bearer    "})
    response = verify_eleven_request(req)
    assert response is not None
    assert response.status_code == 401


def test_wrong_token_returns_401(valid_token: str) -> None:  # noqa: ARG001
    req = _make_request({"authorization": "Bearer wrong-token-here"})
    response = verify_eleven_request(req)
    assert response is not None
    assert response.status_code == 401


def test_correct_token_returns_none(valid_token: str) -> None:
    req = _make_request({"authorization": f"Bearer {valid_token}"})
    response = verify_eleven_request(req)
    assert response is None


def test_uses_timing_safe_compare_digest(valid_token: str) -> None:
    """Spy sur ``hmac.compare_digest`` — vérifie qu'on l'appelle bien
    (vs un ``==`` vulnerable aux timing attacks)."""
    req = _make_request({"authorization": f"Bearer {valid_token}"})

    with patch.object(
        security_module.hmac,
        "compare_digest",
        wraps=hmac.compare_digest,
    ) as spy:
        response = verify_eleven_request(req)

    assert response is None
    spy.assert_called_once()
    # Les deux args sont des bytes (encoding utf-8 par notre middleware).
    args = spy.call_args.args
    assert isinstance(args[0], bytes)
    assert isinstance(args[1], bytes)


def test_token_value_never_in_response_body(valid_token: str) -> None:  # noqa: ARG001
    """Le body 401 ne doit jamais contenir le token reçu."""
    req = _make_request({"authorization": "Bearer attacker-supplied-secret-xyz"})
    response = verify_eleven_request(req)
    assert response is not None
    body = response.body.decode("utf-8")
    parsed = json.loads(body)
    assert "attacker-supplied-secret-xyz" not in body
    # Body est neutre, code stable.
    assert parsed["error"]["type"] == "auth_error"


def test_logger_does_not_emit_received_token(
    valid_token: str,  # noqa: ARG001
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Le logger structlog doit consigner un hash tronqué, jamais la valeur."""
    sentinel = "very-secret-value-do-not-leak-1234567890"
    req = _make_request({"authorization": f"Bearer {sentinel}"})
    with caplog.at_level("INFO"):
        verify_eleven_request(req)
    # Aucun message de log ne contient la valeur littérale.
    for record in caplog.records:
        assert sentinel not in record.getMessage()
        assert sentinel not in str(record.args or "")
