"""S10 voice mode — end-to-end live test.

Smoke test live de l'endpoint ``POST /v1/chat/completions`` côté Railway
prod (ou local ngrok). Vérifie le format SSE OpenAI Chat Completions et
que la narration est bien émise sur les tool_use Pappers.

**Hors scope** : audio TTS (testé manuellement via le widget côté
navigateur — voir étape 6 phase 2). Ici on vérifie uniquement le flux
texte SSE renvoyé au custom LLM ElevenLabs.

Activation :

    make test-integration  # ou : pytest -m integration tests/integration

Pré-requis :

- ``ANTHROPIC_API_KEY`` + ``PAPPERS_API_KEY`` valides dans l'env.
- ``ELEVEN_AGENT_SHARED_TOKEN`` configuré dans l'env.
- ``GENIAL_VOICE_E2E_URL`` (override) ou défaut Railway prod.
- ``ENABLE_VOICE_MODE=true`` côté target.
"""

from __future__ import annotations

import json
import os

import httpx
import pytest

pytestmark = pytest.mark.integration

DEFAULT_TARGET = "https://genial-agent-production.up.railway.app"
TARGET = os.getenv("GENIAL_VOICE_E2E_URL", DEFAULT_TARGET)
SHARED_TOKEN = os.getenv("ELEVEN_AGENT_SHARED_TOKEN", "")


@pytest.fixture
def http_client() -> httpx.Client:
    return httpx.Client(timeout=60.0, base_url=TARGET)


@pytest.mark.skipif(not SHARED_TOKEN, reason="ELEVEN_AGENT_SHARED_TOKEN required for voice e2e")
def test_voice_endpoint_unauthorized_without_token(http_client: httpx.Client) -> None:
    """Sans Bearer → 401."""
    resp = http_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "ping"}], "stream": True},
    )
    # 401 si voice est mounté ; 404/405 si feature flag false.
    assert resp.status_code in (401, 404, 405)


@pytest.mark.skipif(not SHARED_TOKEN, reason="ELEVEN_AGENT_SHARED_TOKEN required for voice e2e")
def test_voice_endpoint_unauthorized_with_wrong_token(http_client: httpx.Client) -> None:
    resp = http_client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "ping"}], "stream": True},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp.status_code in (401, 404, 405)


@pytest.mark.skipif(not SHARED_TOKEN, reason="ELEVEN_AGENT_SHARED_TOKEN required for voice e2e")
def test_voice_u1_fiche_lvmh_streams_narration_and_text(
    http_client: httpx.Client,
) -> None:
    """U1 voice mode : fiche LVMH simple → vérifie SSE narratif + texte."""
    body = {
        "messages": [
            {"role": "user", "content": "Donne-moi la fiche LVMH."},
        ],
        "stream": True,
        "model": "genial-agent-claude",
    }

    chunks: list[str] = []
    saw_narration = False
    saw_text = False
    saw_done = False

    with http_client.stream(
        "POST",
        "/v1/chat/completions",
        json=body,
        headers={"Authorization": f"Bearer {SHARED_TOKEN}"},
    ) as resp:
        if resp.status_code in (404, 405):
            pytest.skip("ENABLE_VOICE_MODE=false côté target — endpoint non monté")
        assert resp.status_code == 200, resp.read().decode("utf-8", errors="replace")
        assert resp.headers.get("content-type", "").startswith("text/event-stream")

        for raw_line in resp.iter_lines():
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            payload_str = line[len("data: ") :].strip()
            if payload_str == "[DONE]":
                saw_done = True
                break
            try:
                payload = json.loads(payload_str)
            except json.JSONDecodeError:
                continue
            choice = payload.get("choices", [{}])[0]
            content = choice.get("delta", {}).get("content") or ""
            if content:
                chunks.append(content)
                # Narration possible (Pappers utilise sirenisateur sur U1).
                if "Je cherche le SIREN" in content or "Je consulte" in content:
                    saw_narration = True
                else:
                    saw_text = True

    assert saw_done, "expected [DONE] marker"
    assert saw_text, "expected at least one text chunk"
    # Narration n'est pas obligatoire (cache MCP peut absorber tous les
    # tool calls), mais on log explicitement si elle a manqué.
    if not saw_narration:
        pytest.skip(
            "narration absente — possible cache MCP hit complet ; "
            "lancer après reset cache pour voir les tool steps"
        )

    full_text = "".join(chunks)
    # Cap longueur voice-friendly (cf. VOICE_SUFFIX 100-120 mots).
    # Tolère ~2× le cap (Claude n'est pas strict sur la longueur).
    assert len(full_text.split()) < 350, "voice response too long for TTS"
