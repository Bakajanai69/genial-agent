"""Validation statique de ``railway.json`` (S08).

On parse le fichier comme JSON pur et on assert sur les champs critiques :

- builder Docker.
- healthcheck path et timeout cohérents avec /health (S07).
- politique de restart bornée.
- région Amsterdam explicite via ``multiRegionConfig``.
- pas de sleep (cahier §17.1 keep-alive).
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RAILWAY_JSON = REPO_ROOT / "railway.json"


def _load() -> dict:
    return json.loads(RAILWAY_JSON.read_text(encoding="utf-8"))


def test_railway_json_exists() -> None:
    assert RAILWAY_JSON.is_file(), "railway.json doit être présent à la racine."


def test_schema_url_pinned() -> None:
    cfg = _load()
    assert cfg["$schema"] == "https://railway.com/railway.schema.json"


def test_dockerfile_builder() -> None:
    cfg = _load()
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "Dockerfile"


def test_healthcheck_targets_S07_endpoint() -> None:
    cfg = _load()
    deploy = cfg["deploy"]
    assert deploy["healthcheckPath"] == "/health"
    # Marge confortable vs notre cap interne 3s sur le ping MCP
    # (routes.py:_HEALTH_MCP_TIMEOUT_S) + le boot Chainlit.
    assert deploy["healthcheckTimeout"] >= 30


def test_restart_policy_bounded() -> None:
    cfg = _load()
    deploy = cfg["deploy"]
    assert deploy["restartPolicyType"] in {"ON_FAILURE", "ALWAYS"}
    assert 1 <= deploy["restartPolicyMaxRetries"] <= 10


def test_region_amsterdam_explicit() -> None:
    cfg = _load()
    multi = cfg["deploy"]["multiRegionConfig"]
    assert "europe-west4-drams3a" in multi, (
        "La région Amsterdam (europe-west4-drams3a) doit être présente "
        "dans multiRegionConfig — cf. cahier §6.3."
    )
    # Single replica MVP (sticky sessions hors scope).
    assert multi["europe-west4-drams3a"]["numReplicas"] == 1


def test_sleep_disabled() -> None:
    """Cahier §17.1 : keep-alive UptimeRobot pour éviter le cold start.
    L'inverse — laisser sleepApplication à True — réveille le débat sur
    le Hobby vs Trial plan. On le force à False côté config-as-code."""
    cfg = _load()
    assert cfg["deploy"].get("sleepApplication") is False
