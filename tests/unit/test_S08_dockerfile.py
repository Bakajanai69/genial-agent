"""Vérifications statiques du ``Dockerfile`` (S08).

On ne build pas l'image (test integration séparé) — on grep le contenu
pour les invariants critiques : non-root, shell-form CMD, flag -h
Chainlit, HEALTHCHECK directive, pas de secret hardcodé.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"


def _read() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists() -> None:
    assert DOCKERFILE.is_file()


def test_non_root_user() -> None:
    content = _read()
    assert re.search(r"^USER\s+agent\s*$", content, re.MULTILINE), (
        "Le runtime stage doit basculer en USER agent (uid 1000)."
    )
    assert "useradd" in content


def test_cmd_shell_form_for_port_expansion() -> None:
    """Railway injecte $PORT au runtime. L'expansion ne fonctionne
    qu'en shell-form CMD (sh -c '...'). En exec-form, ${PORT} est
    passé littéralement à Chainlit qui crashe."""
    content = _read()
    # CMD qui commence par ["sh", "-c", ...]
    assert re.search(r'CMD\s*\[\s*"sh"\s*,\s*"-c"', content), (
        "CMD doit être en shell-form ['sh', '-c', '…'] pour expansion ${PORT}."
    )
    assert "${PORT" in content, "Le CMD doit référencer ${PORT}."


def test_chainlit_dash_h_flag() -> None:
    """Cf. docs.chainlit.io/deploy/overview — `-h` empêche l'ouverture
    browser server-side en prod."""
    content = _read()
    assert re.search(r"chainlit\s+run[^\n]*\s-h\b", content), (
        "Le flag -h doit être présent dans la commande chainlit run."
    )


def test_healthcheck_directive_present() -> None:
    content = _read()
    assert re.search(r"^HEALTHCHECK\s", content, re.MULTILINE)
    assert "/health" in content


def test_healthcheck_has_start_period() -> None:
    """Sans --start-period, Docker marque l'image unhealthy avant que
    Chainlit + 1er ping Pappers aient eu le temps de boot."""
    content = _read()
    assert "--start-period" in content


def test_no_hardcoded_secret() -> None:
    """Aucune clé API ne doit fuiter via ENV/ARG dans l'image."""
    content = _read()
    forbidden = [
        "ANTHROPIC_API_KEY=",
        "PAPPERS_API_KEY=",
        "ELEVENLABS_API_KEY=",
        "STATS_TOKEN=",
    ]
    # On accepte ENV PORT=… et ARG PYTHON_VERSION=… (pas de secret).
    for pat in forbidden:
        assert pat not in content, f"Secret hardcodé détecté : {pat!r}"


def test_chainlit_config_copied() -> None:
    """`.chainlit/config.toml` (S06 customizations) doit être dans l'image."""
    content = _read()
    assert re.search(r"COPY[^\n]*\.chainlit", content), (
        ".chainlit/ doit être COPY-é dans le runtime stage."
    )


def test_dockerignore_exists() -> None:
    assert DOCKERIGNORE.is_file()


def test_dockerignore_excludes_secrets() -> None:
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    assert ".env" in content
    assert ".venv/" in content
    assert ".git/" in content


def test_dockerignore_keeps_chainlit_config() -> None:
    """Régression review S08 : .chainlit/ ne doit pas être exclu en bloc.
    Sinon Chainlit tourne en prod avec ses défauts (allow_origins, MCP
    client-side enabled, spontaneous file upload), perte des hardenings
    S06. On vérifie que la ligne d'exclusion est bien sur un sous-path
    (artefacts runtime), pas sur le répertoire entier."""
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    # Aucun pattern qui matche .chainlit/ ou .chainlit/* en bloc.
    forbidden_patterns = {".chainlit", ".chainlit/", ".chainlit/*"}
    assert not (forbidden_patterns & set(lines)), (
        ".dockerignore ne doit pas exclure .chainlit/ en bloc — cela embarque "
        "config.toml et perd les hardenings S06."
    )


def test_dockerignore_keeps_readme() -> None:
    """``uv sync --frozen`` dans le builder lit README.md (hatchling
    requirement). Si .dockerignore l'exclut sans le ré-inclure, le COPY
    du builder stage échoue."""
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    assert "!README.md" in content, (
        "README.md doit être ré-inclus (requis par hatchling à uv sync)."
    )
