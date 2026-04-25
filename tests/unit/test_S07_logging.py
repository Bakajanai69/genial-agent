"""Tests de la chaîne ``structlog`` (S07 §"Chaîne structlog 25.5 retenue").

Couvre :

- PII scrub appliqué (S05) ;
- ``EventRenamer(to="msg")`` actif ;
- ``merge_contextvars`` injecte session_id / request_id ;
- timestamp ISO 8601 présent ;
- ``add_log_level`` ajoute le champ ``level``.
"""

from __future__ import annotations

import json

import pytest
import structlog

from genial_agent.observability.logging import configure_logging, reset_for_tests


@pytest.fixture(autouse=True)
def _ensure_logging_configured(capsys: pytest.CaptureFixture[str]) -> None:  # noqa: ARG001
    """Reconfigure structlog par test : ``capsys`` peut perturber les
    handlers stdlib si la configuration est mémoïsée d'un test à l'autre.
    On purge ``contextvars`` pour éviter la fuite cross-test."""
    reset_for_tests()
    configure_logging()
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


def _last_json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "no log output captured"
    return json.loads(out[-1])


def test_pii_email_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().info("evt", email="leak@example.com")
    line = _last_json_line(capsys)
    assert line["email"] == "[EMAIL]"


def test_pii_phone_scrubbed(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().info("evt", phone="06 12 34 56 78")
    line = _last_json_line(capsys)
    assert line["phone"] == "[PHONE_FR]"


def test_event_renamed_to_msg(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().info("test_event")
    line = _last_json_line(capsys)
    assert line["msg"] == "test_event"
    assert "event" not in line


def test_contextvars_merged_into_log(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.contextvars.bind_contextvars(session_id="abc", request_id="req-123")
    structlog.get_logger().info("scoped_event")
    line = _last_json_line(capsys)
    assert line["session_id"] == "abc"
    assert line["request_id"] == "req-123"


def test_iso_timestamp_present(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().info("ts_event")
    line = _last_json_line(capsys)
    assert "timestamp" in line
    # ISO 8601 UTC : contient 'T' et finit par 'Z' ou '+00:00'.
    ts = str(line["timestamp"])
    assert "T" in ts
    assert ts.endswith("Z") or "+00:00" in ts or "+0000" in ts


def test_log_level_present(capsys: pytest.CaptureFixture[str]) -> None:
    structlog.get_logger().warning("warn_event")
    line = _last_json_line(capsys)
    assert line["level"] == "warning"


def test_configure_logging_is_idempotent() -> None:
    """Régression : ``app.py`` peut être ré-importé en test (TestClient,
    fixtures multiples) ; ``configure_logging`` doit être no-op au 2ᵉ
    appel."""
    configure_logging()
    configure_logging()  # no exception, no effect


def test_pii_scrubbed_in_message_string(capsys: pytest.CaptureFixture[str]) -> None:
    """Le scrub porte sur **toutes** les valeurs ``str`` du event_dict, y
    compris le ``msg`` lui-même (après EventRenamer)."""
    structlog.get_logger().info("contacted leak@example.com about IBAN")
    line = _last_json_line(capsys)
    assert "leak@example.com" not in str(line)
    assert "[EMAIL]" in str(line["msg"])


def test_httpx_info_logger_is_silenced() -> None:
    """``httpx`` logue à INFO ``HTTP Request: POST <url>`` — l'URL Pappers
    contient la clé API dans le path. Régression pour cahier R2 + S07
    invariant "L'URL Pappers complète n'est jamais loggée"."""
    import logging as _logging

    assert _logging.getLogger("httpx").getEffectiveLevel() >= _logging.WARNING
    assert _logging.getLogger("httpcore").getEffectiveLevel() >= _logging.WARNING
