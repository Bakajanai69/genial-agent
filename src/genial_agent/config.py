"""Chargement centralisé des variables d'environnement.

Source unique : `.env` local (gitignoré) + variables Railway en prod. Les
modules consomment `settings.XXX` et **jamais** `os.getenv(...)` direct.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    PAPPERS_API_KEY: str = os.getenv("PAPPERS_API_KEY", "")
    ELEVENLABS_API_KEY: str = os.getenv("ELEVENLABS_API_KEY", "")
    ELEVENLABS_VOICE_GAELLE: str = os.getenv("ELEVENLABS_VOICE_GAELLE", "")
    ELEVENLABS_VOICE_GUILLAUME: str = os.getenv("ELEVENLABS_VOICE_GUILLAUME", "")
    ELEVENLABS_MODEL_ID: str = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    # S10 — voice mode conversationnel (Eleven Agents). Renommé depuis
    # ``ENABLE_VOICE_BRIEF`` (S10 v1, abandonné au profit du voice mode v2).
    ENABLE_VOICE_MODE: bool = os.getenv("ENABLE_VOICE_MODE", "false").lower() == "true"
    # S10 — ID public de l'Eleven Agent créé côté dashboard ElevenLabs.
    # Format ``agent_xxxxxxxxxxxxxxxxxxxxx``. Pas un secret (consommable
    # par n'importe quel widget — la sécurité passe par le domain
    # allowlist côté ElevenLabs + le Bearer token côté custom LLM).
    ELEVEN_AGENT_ID: str = os.getenv("ELEVEN_AGENT_ID", "")
    # S10 — Token partagé entre Workspace Secret ElevenLabs et notre
    # endpoint ``/v1/chat/completions``. Vérifié timing-safe via
    # ``hmac.compare_digest`` côté middleware ``voice/security.py``.
    # 32+ chars (``python -c "import secrets; print(secrets.token_urlsafe(32))"``).
    ELEVEN_AGENT_SHARED_TOKEN: str = os.getenv("ELEVEN_AGENT_SHARED_TOKEN", "")


settings = Settings()
