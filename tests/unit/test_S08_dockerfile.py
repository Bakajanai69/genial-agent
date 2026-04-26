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
    """Le runtime doit s'exécuter en agent (uid 1000), pas root.

    S09.7 hotfix : on autorise les 2 patterns équivalents :
    1. ``USER agent`` directive Dockerfile (pattern historique S08).
    2. ``ENTRYPOINT /entrypoint.sh`` qui setpriv → uid 1000 (pattern
       S09.7, nécessaire pour chown /data au boot avant switch user).
    Dans les 2 cas, le user effectif au runtime de Chainlit reste
    agent uid 1000.
    """
    content = _read()
    assert "useradd" in content, "user agent (uid 1000) doit être créé"
    has_user_directive = bool(re.search(r"^USER\s+agent\s*$", content, re.MULTILINE))
    has_setpriv_entrypoint = bool(
        re.search(r"^ENTRYPOINT\s+\[\s*\"/entrypoint\.sh\"\s*\]\s*$", content, re.MULTILINE)
    )
    assert has_user_directive or has_setpriv_entrypoint, (
        "Le runtime stage doit basculer en agent (uid 1000) via USER directive "
        "OU via /entrypoint.sh + setpriv (pattern S09.7 hotfix volume Railway)."
    )


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
    """Aucune clé API ne doit fuiter via ``ENV`` ou ``ARG`` dans l'image.

    Q3 review S08 : la version originale faisait un grep brut
    (``"ANTHROPIC_API_KEY=" not in content``) qui rejetait même les
    commentaires défensifs (ex : ``# do not set ANTHROPIC_API_KEY=…``).
    On regex-match maintenant uniquement les vraies instructions
    ``ENV`` ou ``ARG`` qui assignent une valeur — un commentaire
    documentaire passe.
    """
    content = _read()
    # ``(ENV|ARG)`` au début d'une ligne (ignorant les espaces) suivi
    # d'un nom de var sensible avec ``=<valeur>`` (=… non vide).
    secret_names = (
        "ANTHROPIC_API_KEY",
        "PAPPERS_API_KEY",
        "ELEVENLABS_API_KEY",
        "STATS_TOKEN",
        "RAILWAY_API_TOKEN",
    )
    for name in secret_names:
        # ENV NAME=value | ARG NAME=value | ENV NAME value | ARG NAME value
        bad_pattern = re.compile(
            rf"^\s*(ENV|ARG)\s+{re.escape(name)}\s*[=\s]\s*\S+",
            re.MULTILINE,
        )
        match = bad_pattern.search(content)
        assert match is None, (
            f"Secret hardcodé détecté : {match.group(0)!r}. "
            f"Les valeurs de {name} doivent venir des Variables Railway, "
            "pas du Dockerfile."
        )


def test_chainlit_config_copied() -> None:
    """`.chainlit/config.toml` (S06 customizations) doit être dans l'image."""
    content = _read()
    assert re.search(r"COPY[^\n]*\.chainlit", content), (
        ".chainlit/ doit être COPY-é dans le runtime stage."
    )


def test_dockerignore_exists() -> None:
    assert DOCKERIGNORE.is_file()


def test_dockerignore_excludes_secrets() -> None:
    """Q4 review S08 : couvrir aussi les variantes ``.env.production``,
    ``.env.local`` (matchées par ``.env.*``) sinon une régression silencieuse
    pourrait embarquer des secrets dev/staging dans l'image."""
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    assert ".env" in lines, ".dockerignore doit exclure .env (secret runtime)."
    assert ".env.*" in lines, (
        ".dockerignore doit exclure .env.* (couvre .env.production, .env.local, etc.)."
    )
    assert ".venv/" in lines
    assert ".git/" in lines


def test_dockerignore_keeps_chainlit_config() -> None:
    """Régression review S08 : ``.chainlit/`` ne doit pas être exclu en
    bloc, ni ``config.toml`` ciblément. Sinon Chainlit tourne en prod
    avec ses défauts (allow_origins, MCP client-side enabled, spontaneous
    file upload), perte des hardenings S06.

    Q5 review S08 : version durcie qui couvre aussi le pattern d'exclusion
    ciblée ``.chainlit/config.toml`` (contournement de la version naïve).
    """
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    lines = [ln.strip() for ln in content.splitlines() if ln.strip() and not ln.startswith("#")]
    forbidden_patterns = {
        ".chainlit",
        ".chainlit/",
        ".chainlit/*",
        ".chainlit/**",
        ".chainlit/config.toml",
        "**/.chainlit/config.toml",
    }
    intersection = forbidden_patterns & set(lines)
    assert not intersection, (
        f".dockerignore ne doit pas exclure {intersection} — cela perd les "
        "hardenings S06 (allow_origins, MCP off, no upload). Lister uniquement "
        "les sous-paths runtime (.session_files/, etc.)."
    )


def test_dockerignore_keeps_readme() -> None:
    """``uv sync --frozen`` dans le builder lit README.md (hatchling
    requirement). Si .dockerignore l'exclut sans le ré-inclure, le COPY
    du builder stage échoue."""
    content = DOCKERIGNORE.read_text(encoding="utf-8")
    assert "!README.md" in content, (
        "README.md doit être ré-inclus (requis par hatchling à uv sync)."
    )
