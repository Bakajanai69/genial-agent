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
    ENABLE_VOICE_BRIEF: bool = os.getenv("ENABLE_VOICE_BRIEF", "false").lower() == "true"


settings = Settings()
