"""Compteurs cumulatifs in-memory pour ``/stats`` et le mode dégradé.

API **synchrone** : asyncio est cooperative, ``setattr`` sur un ``int``
est atomique entre yield points. La cohérence du snapshot est
best-effort (lecture multi-champ peut surprendre un ``incr`` en cours)
— acceptable pour ``/stats`` en démo, ce n'est pas un compteur de
facturation.

Cohérent avec le contrat sync de ``mcp_pappers._is_degraded()`` (S02
review C4) : aucune transition async dans le hot-path Pappers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


@dataclass
class Stats:
    started_at_iso: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at_monotonic: float = field(default_factory=time.monotonic)
    # Tour utilisateur servi : 1 message reçu et engagé dans le pipeline
    # (hors cache hit idempotence, hors message vide). Incrémenté par
    # ``app.py:on_message`` — **jamais** par le pipeline (cf. review B1).
    total_turns: int = 0
    # Appel LLM Anthropic effectif : 1 par event ``llm_meta`` reçu par le
    # pipeline (1 turn utilisateur peut générer N appels selon la chaîne
    # de tool calls + escalade éventuelle). Incrémenté par
    # ``pipeline.py``.
    total_llm_calls: int = 0
    total_tool_calls: int = 0
    pappers_calls_today: int = 0
    pappers_calls_today_day: str = field(default_factory=_today)
    anthropic_input_tokens: int = 0
    anthropic_output_tokens: int = 0
    errors: int = 0
    # S09.5 — instrumentation Payload Vault. ``payloads_offloaded_total``
    # = nombre de tool results MCP rangés dans le vault parce que >
    # ``OFFLOAD_THRESHOLD_CHARS``. ``payload_inspects_total`` /
    # ``payload_searches_total`` = nombre de lookups de l'agent dans
    # le vault. Le ratio inspects/offloaded donne un signal qualité :
    # ~1-3 inspects/offload = bon usage, > 5 = l'agent peine.
    payloads_offloaded_total: int = 0
    payload_inspects_total: int = 0
    payload_searches_total: int = 0
    # S09.7 — instrumentation prompt caching Anthropic. Permet de
    # mesurer le ROI du cache_control posé sur tools+system+messages[-1].
    # ``cache_creation`` = tokens facturés à 1.25× input price (write).
    # ``cache_read`` = tokens facturés à 0.1× input price (read 90% off).
    # Cible : ratio ``cache_read / (input_tokens + cache_read) > 50%``
    # à partir du 2ème round (cf. story §"Mesure prompt caching").
    anthropic_cache_creation_tokens: int = 0
    anthropic_cache_read_tokens: int = 0
    # S10 — voice mode (Eleven Agents). Compteurs incrémentés au
    # call-site dans ``voice/openai_adapter.py`` (cohérent avec la
    # règle README #3 "compteurs au call-site"). Les minutes audio
    # ElevenLabs ne sont **pas** mesurées côté nous : ElevenLabs facture
    # en minutes côté leur dashboard, on ne re-mesure pas.
    voice_sessions_total: int = 0
    voice_custom_llm_calls: int = 0
    voice_chars_tts: int = 0
    voice_narration_chunks_emitted: int = 0
    voice_cancelled_total: int = 0
    # Pass 2 reformulator a échoué (Anthropic 429/503 typique) → fallback
    # main strippé. Permet de surveiller la santé du Haiku reformulateur
    # sans inspecter les logs structurés.
    voice_reformulator_failed: int = 0


_stats = Stats()


_INCR_FIELDS = frozenset(
    {
        "total_turns",
        "total_llm_calls",
        "total_tool_calls",
        "pappers_calls_today",
        "anthropic_input_tokens",
        "anthropic_output_tokens",
        "errors",
        "payloads_offloaded_total",
        "payload_inspects_total",
        "payload_searches_total",
        "anthropic_cache_creation_tokens",
        "anthropic_cache_read_tokens",
        "voice_sessions_total",
        "voice_custom_llm_calls",
        "voice_chars_tts",
        "voice_narration_chunks_emitted",
        "voice_cancelled_total",
        "voice_reformulator_failed",
    }
)


def _rollover_day_if_needed() -> None:
    today = _today()
    if _stats.pappers_calls_today_day != today:
        _stats.pappers_calls_today = 0
        _stats.pappers_calls_today_day = today


def incr(**kwargs: int) -> None:
    """Incrémente atomiquement les compteurs nommés.

    Champs reconnus : ``total_turns``, ``total_tool_calls``,
    ``pappers_calls_today``, ``anthropic_input_tokens``,
    ``anthropic_output_tokens``, ``errors``. Une typo lève
    ``AttributeError`` au runtime (failsafe).
    """
    _rollover_day_if_needed()
    for k, v in kwargs.items():
        if k not in _INCR_FIELDS:
            raise AttributeError(f"unknown stat: {k}")
        setattr(_stats, k, getattr(_stats, k) + v)


def snapshot() -> dict[str, int | str]:
    """Retourne un snapshot des compteurs pour ``/stats``."""
    _rollover_day_if_needed()
    return {
        "started_at": _stats.started_at_iso,
        "uptime_s": int(time.monotonic() - _stats.started_at_monotonic),
        "total_turns": _stats.total_turns,
        "total_llm_calls": _stats.total_llm_calls,
        "total_tool_calls": _stats.total_tool_calls,
        "pappers_calls_today": _stats.pappers_calls_today,
        "pappers_calls_today_day": _stats.pappers_calls_today_day,
        "anthropic_input_tokens": _stats.anthropic_input_tokens,
        "anthropic_output_tokens": _stats.anthropic_output_tokens,
        "errors": _stats.errors,
        "payloads_offloaded_total": _stats.payloads_offloaded_total,
        "payload_inspects_total": _stats.payload_inspects_total,
        "payload_searches_total": _stats.payload_searches_total,
        "anthropic_cache_creation_tokens": _stats.anthropic_cache_creation_tokens,
        "anthropic_cache_read_tokens": _stats.anthropic_cache_read_tokens,
        "voice_sessions_total": _stats.voice_sessions_total,
        "voice_custom_llm_calls": _stats.voice_custom_llm_calls,
        "voice_chars_tts": _stats.voice_chars_tts,
        "voice_narration_chunks_emitted": _stats.voice_narration_chunks_emitted,
        "voice_cancelled_total": _stats.voice_cancelled_total,
        "voice_reformulator_failed": _stats.voice_reformulator_failed,
    }


def pappers_calls_today() -> int:
    """Compteur d'appels Pappers du jour UTC courant.

    Source de vérité de ``credit_guard.degraded()``.
    """
    _rollover_day_if_needed()
    return _stats.pappers_calls_today


def reset_for_tests() -> None:
    """Reset complet — utilisé par la fixture ``_fresh_stats`` du conftest."""
    global _stats
    _stats = Stats()
