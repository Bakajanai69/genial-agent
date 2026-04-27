"""Build + run du conteneur, smoke /health (marker integration).

Skip si :

- ``docker`` introuvable dans le PATH ;
- ``ANTHROPIC_API_KEY`` ou ``PAPPERS_API_KEY`` absent (Pappers MCP
  ping requis pour /health → status:ok).

Test opt-in : ``make test-integration``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_TAG = "genial-agent:test-S08"
HOST_PORT = 8765

# Q2 review S08 : un Docker daemon qui hang ferait freezer pytest
# indéfiniment. On borne explicitement les sous-process via timeout.
_BUILD_TIMEOUT_S = 600  # 10 min — image neuve sur cold cache
_RUN_TIMEOUT_S = 30  # docker run -d retourne presque instantanément


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _api_keys_present() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") and os.getenv("PAPPERS_API_KEY"))


@pytest.fixture(scope="module")
def docker_container():
    if not _docker_available():
        pytest.skip("Docker non disponible — install + start docker daemon.")
    if not _api_keys_present():
        pytest.skip("ANTHROPIC_API_KEY ou PAPPERS_API_KEY absent.")

    subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, "."],
        check=True,
        cwd=str(REPO_ROOT),
        timeout=_BUILD_TIMEOUT_S,
    )

    cid = subprocess.check_output(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "-p",
            f"{HOST_PORT}:8000",
            "-e",
            f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}",
            "-e",
            f"PAPPERS_API_KEY={os.environ['PAPPERS_API_KEY']}",
            "-e",
            "LOG_LEVEL=INFO",
            "-e",
            "ENABLE_VOICE_MODE=false",
            IMAGE_TAG,
        ],
        text=True,
        timeout=_RUN_TIMEOUT_S,
    ).strip()

    deadline = time.monotonic() + 30
    last_err: Exception | None = None
    health_up = False
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://localhost:{HOST_PORT}/health", timeout=2) as r:
                if r.status == 200:
                    health_up = True
                    break
        except (urllib.error.URLError, ConnectionRefusedError, TimeoutError) as exc:
            last_err = exc
            time.sleep(1)

    if not health_up:
        subprocess.run(["docker", "logs", cid], check=False)
        subprocess.run(["docker", "stop", cid], check=False)
        pytest.fail(f"/health pas up en 30 s : {last_err}")

    yield cid

    subprocess.run(["docker", "stop", cid], check=False)


def test_health_returns_ok(docker_container):
    with urllib.request.urlopen(f"http://localhost:{HOST_PORT}/health", timeout=5) as r:
        body = json.loads(r.read())
    assert r.status == 200
    assert set(body.keys()) >= {"status", "mcp", "version", "uptime_s"}
    assert body["status"] == "ok"
    assert body["mcp"]["tools_count"] >= 1


def test_chainlit_root_serves(docker_container):
    """`/` doit servir l'UI Chainlit — preuve que le catch-all natif
    n'est pas shadow-é par notre prepend route."""
    with urllib.request.urlopen(f"http://localhost:{HOST_PORT}/", timeout=5) as r:
        assert r.status == 200
        body = r.read().decode("utf-8", errors="ignore")
    # L'UI Chainlit charge index.html avec quelques signes distinctifs.
    assert "<html" in body.lower()
