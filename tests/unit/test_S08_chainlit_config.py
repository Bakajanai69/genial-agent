"""Tests des hardenings ``.chainlit/config.toml`` (S08 review §B3).

L'image Railway embarque ``.chainlit/config.toml`` (cf. Dockerfile S08).
Les hardenings de ce fichier sont donc actifs en prod — toute
régression vers les défauts Chainlit ou un wildcard CORS rouvrirait
des surfaces (CSRF Socket.IO, clickjacking, exécution MCP côté client).

Ces tests **lisent le fichier statiquement** (pas via ``chainlit.config``)
pour ne dépendre d'aucune init Chainlit ni du flag ``-c`` éventuel.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAINLIT_CONFIG = REPO_ROOT / ".chainlit" / "config.toml"


@pytest.fixture(scope="module")
def cfg() -> dict:
    return tomllib.loads(CHAINLIT_CONFIG.read_text(encoding="utf-8"))


def test_chainlit_config_exists() -> None:
    assert CHAINLIT_CONFIG.is_file(), (
        ".chainlit/config.toml doit être versionné (cf. S06 hardenings + S08 image)."
    )


def test_allow_origins_is_explicit_list_no_wildcard(cfg: dict) -> None:
    """Régression S08 §B3 : ``allow_origins = ["*"]`` rouvre le chat à
    n'importe quel site malveillant (iframe, fetch). On exige une liste
    explicite et **pas** de wildcard."""
    origins = cfg["project"]["allow_origins"]
    assert isinstance(origins, list)
    assert origins, "allow_origins ne doit pas être vide (sinon Chainlit refuse tout)."
    assert "*" not in origins, (
        "Régression S08 §B3 : wildcard détecté dans allow_origins. "
        "Lister explicitement le domaine Railway prod + localhost dev."
    )


def test_allow_origins_includes_railway_domain(cfg: dict) -> None:
    """Le domaine prod Railway doit être listé pour que la démo
    fonctionne en HTTPS sur ``genial-agent-production.up.railway.app``."""
    origins = cfg["project"]["allow_origins"]
    assert any(o.startswith("https://") and "railway.app" in o for o in origins), (
        "Au moins une entrée HTTPS .railway.app doit être présente dans allow_origins."
    )


def test_allow_origins_uses_https_for_public_entries(cfg: dict) -> None:
    """Toute origin non-localhost doit être en HTTPS (pas de cleartext
    en prod)."""
    origins = cfg["project"]["allow_origins"]
    for o in origins:
        if "localhost" in o or "127.0.0.1" in o:
            continue
        assert o.startswith("https://"), f"Origin prod non-HTTPS : {o!r}"


def test_chainlit_mcp_disabled(cfg: dict) -> None:
    """Régression S06 : tous les modes MCP côté Chainlit (sse, stdio,
    streamable-http) restent désactivés. Notre agent appelle son **propre**
    MCP côté serveur — l'utilisateur ne configure rien."""
    mcp = cfg["features"]["mcp"]
    assert mcp["enabled"] is False
    assert mcp["sse"]["enabled"] is False
    assert mcp["stdio"]["enabled"] is False
    assert mcp["streamable-http"]["enabled"] is False
    assert mcp["stdio"]["allowed_executables"] == []


def test_spontaneous_file_upload_disabled(cfg: dict) -> None:
    """Régression S06 : pas de bouton "joindre un fichier" inutile +
    pas de surface d'attaque upload."""
    upload = cfg["features"]["spontaneous_file_upload"]
    assert upload["enabled"] is False
    assert upload["accept"] == []
    assert upload["max_files"] == 0
    assert upload["max_size_mb"] == 0


def test_unsafe_html_disabled(cfg: dict) -> None:
    """Régression S06 : ``unsafe_allow_html = false`` (anti-XSS dans les
    messages assistant)."""
    assert cfg["features"]["unsafe_allow_html"] is False
