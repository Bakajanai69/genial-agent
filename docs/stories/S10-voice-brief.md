# S10 — 🎯 Stretch : brief vocal immersif via ElevenLabs

> **Statut** : ⏸ bloqué par gating
> **Durée estimée** : 1 h 30 – 2 h
> **Parallélisable avec** : —

---

## 📍 Contexte

Feature optionnelle : à chaque réponse, un brief radio de 30-40 s lu
par une voix ElevenLabs (Gaëlle ou Guillaume). Génération via un
briefer Haiku dédié. Hors chemin critique. Transcription toujours
visible en complément (WCAG).

Sources de vérité :
- `docs/cahier-des-charges.md` §19 complet (concept, architecture,
  décisions produit, voix, config, robustesse).
- Stories précédentes S01-S09 toutes approuvées.

---

## 🔒 Prérequis (gating strict §19.1)

Aucun écart toléré. Cocher avant de démarrer :

- [ ] S09 approuvée, URL Railway stable.
- [ ] 3 tests officiels Pappers OK en live.
- [ ] Routing Haiku/Sonnet fonctionnel avec badges UI.
- [ ] Pack adversarial ≥ 8/10.
- [ ] Empty state + starters + SIREN cliquables opérationnels.
- [ ] `EVALUATION.md` rédigé.
- [ ] Healthcheck + keep-alive UptimeRobot actifs.

## 🔑 Inputs utilisateur requis

- [ ] `ELEVENLABS_API_KEY` fournie et active.
- [ ] Solde crédits ElevenLabs ≥ 5 000 chars (vérifié sur leur dashboard).
- [ ] `ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD` dans `.env` (déjà
      en S01).
- [ ] `ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD` dans `.env`.
- [ ] `ENABLE_VOICE_BRIEF=true` dans Railway (et `.env` local).

---

## 🎯 Scope

### Dans le scope

- `src/genial_agent/voice/briefer.py` — génère un script narratif
  ≤ 100 mots via Haiku.
- `src/genial_agent/voice/tts.py` — client ElevenLabs avec
  idempotence, retry, streaming buffer, mapping erreurs.
- `src/genial_agent/voice/fallback.py` — auto-désactivation après 3
  échecs consécutifs.
- Intégration UI dans `app.py` : 5ème starter, toggle settings, lecteur
  audio inline, transcription affichée sous le lecteur.
- Sélecteur de voix Gaëlle / Guillaume dans `cl.ChatSettings`.

### Hors scope

- STT (speech-to-text) — pas d'input vocal.
- Multi-voix par locuteur / alternance — une seule voix par session.
- Streaming chunk-by-chunk vers le navigateur (buffer puis play retenu,
  cf. §19.12.2).

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Version actuelle du SDK Python ElevenLabs (`elevenlabs` package).
- [ ] Endpoint exact : `POST /v1/text-to-speech/{voice_id}/stream` avec
      param `output_format=mp3_22050_32`.
- [ ] Signature de la requête : body JSON avec `text`, `model_id`,
      `voice_settings` (stability, similarity_boost).
- [ ] Codes erreur explicites (401, 402, 422, 429, 500).
- [ ] Rate limits actuels du plan standard (RPS, chars/jour).
- [ ] Compatibilité `cl.Audio` de Chainlit avec bytes MP3 directs ou
      URL data:.
- [ ] Autoplay policy Chrome 2026 : confirmer que le clic sur le
      toggle compte comme interaction utilisateur pour débloquer
      l'autoplay sur les messages suivants.

### Points à résoudre

- [ ] Format MP3 optimal : `mp3_22050_32` (22 kHz, 32 kbps) est un bon
      compromis qualité/taille. À confirmer audibilité.
- [ ] Taille cache : pour 20 briefs par session × ~500 KB, cache mémoire
      de 10 MB suffit. Expiration 10 min.

### Commit phase 1

`story(S10): refine — ElevenLabs SDK 2026, endpoint stream, error codes`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `src/genial_agent/voice/__init__.py`
- `src/genial_agent/voice/briefer.py`
- `src/genial_agent/voice/tts.py`
- `src/genial_agent/voice/fallback.py`
- Modif `src/genial_agent/app.py` (toggle, audio player, settings).
- Modif `src/genial_agent/ui/starters.py` (5ème starter vocal).
- Modif `.env.example` (si pas déjà en S01 phase 1).
- `tests/unit/test_S10_briefer.py`
- `tests/unit/test_S10_tts.py`
- `tests/integration/test_S10_live.py`

### `briefer.py`

```python
"""Briefer Haiku : génère un script radio ≤ 100 mots."""
from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic

from genial_agent.config import settings
from genial_agent.models import MODEL_HAIKU

logger = structlog.get_logger(__name__)

BRIEFER_PROMPT = """Tu es un journaliste financier. Tu rédiges un
brief audio de 30 à 40 secondes à partir des données fournies.

Règles strictes :
- Ne jamais énoncer de SIREN (ils sont faits pour l'œil, pas l'oreille).
- Ne jamais lire un nombre à décimales : arrondis.
- Pas plus de 100 mots.
- Style narratif fluide, pour l'oreille. Pas de bullet points.
- Ton neutre et factuel.
- Conclure par la date du bilan source si pertinent.
- Toujours en français.
"""

MAX_WORDS = 100


async def generate_brief(question: str, response: str) -> str:
    """Renvoie un script narratif ≤ 100 mots."""
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    msg = await client.messages.create(
        model=MODEL_HAIKU,
        max_tokens=256,
        system=BRIEFER_PROMPT,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Question utilisateur :\n{question}\n\n"
                    f"Données extraites :\n{response}\n\n"
                    f"Rédige le brief audio :"
                ),
            }
        ],
    )
    script = msg.content[0].text.strip() if msg.content else ""
    words = script.split()
    if len(words) > MAX_WORDS:
        logger.warning("briefer_over_length", words=len(words))
        script = " ".join(words[:MAX_WORDS]) + "…"
    return script
```

### `tts.py`

```python
"""Client ElevenLabs TTS avec idempotence, retry, mapping erreurs."""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from genial_agent.config import settings

logger = structlog.get_logger(__name__)

BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_MODEL = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT = "mp3_22050_32"
CONNECT_TIMEOUT_S = 5
TOTAL_TIMEOUT_S = 30


class ElevenLabsError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class QuotaExhausted(ElevenLabsError):
    pass


class AuthError(ElevenLabsError):
    pass


class RateLimited(ElevenLabsError):
    pass


class TransientError(ElevenLabsError):
    pass


# Idempotence cache
@dataclass
class _CacheEntry:
    audio: bytes
    expires_at: float


_cache: dict[str, _CacheEntry] = {}
_CACHE_TTL_S = 60


def _cache_key(text: str, voice_id: str, model_id: str) -> str:
    return hashlib.sha256(f"{text}|{voice_id}|{model_id}".encode()).hexdigest()


def _map_error(response: httpx.Response) -> ElevenLabsError:
    status = response.status_code
    body = response.text[:200]
    if status == 401:
        return AuthError(f"auth_failed: {body}", status=status)
    if status == 402:
        return QuotaExhausted(f"quota_exhausted: {body}", status=status)
    if status == 422:
        return ElevenLabsError(f"invalid_payload: {body}", status=status)
    if status == 429:
        return RateLimited(f"rate_limited: {body}", status=status)
    if 500 <= status < 600:
        return TransientError(f"server_error_{status}: {body}", status=status)
    return ElevenLabsError(f"unexpected_{status}: {body}", status=status)


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=0.5, max=2.0),
    retry=retry_if_exception_type((RateLimited, TransientError, httpx.TimeoutException)),
)
async def _post_with_retry(client: httpx.AsyncClient, url: str, json: dict, headers: dict) -> bytes:
    response = await client.post(url, json=json, headers=headers, timeout=TOTAL_TIMEOUT_S)
    if response.status_code == 200:
        return response.content
    err = _map_error(response)
    if isinstance(err, (AuthError, QuotaExhausted, ElevenLabsError)) and not isinstance(err, (RateLimited, TransientError)):
        raise err  # pas de retry sur 4xx logiques
    raise err


async def synthesize(
    text: str,
    voice_id: str,
    model_id: str = DEFAULT_MODEL,
) -> tuple[bytes, bool]:
    """Retourne (audio_bytes, was_cached). Lève ElevenLabsError sur échec."""
    if not settings.ELEVENLABS_API_KEY:
        raise AuthError("ELEVENLABS_API_KEY not set")

    key = _cache_key(text, voice_id, model_id)
    entry = _cache.get(key)
    if entry and entry.expires_at > time.monotonic():
        logger.info("elevenlabs_cache_hit", voice_id=voice_id, text_length=len(text))
        return entry.audio, True

    url = f"{BASE_URL}/{voice_id}/stream?output_format={DEFAULT_OUTPUT_FORMAT}"
    headers = {"xi-api-key": settings.ELEVENLABS_API_KEY, "accept": "audio/mpeg"}
    json_body = {"text": text, "model_id": model_id}

    start = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)) as client:
        audio = await _post_with_retry(client, url, json_body, headers)

    _cache[key] = _CacheEntry(audio=audio, expires_at=time.monotonic() + _CACHE_TTL_S)
    logger.info(
        "elevenlabs_tts_ok",
        voice_id=voice_id,
        text_length=len(text),
        latency_ms=int((time.monotonic() - start) * 1000),
        cached=False,
    )
    return audio, False
```

### `fallback.py`

```python
"""Auto-désactivation après N échecs consécutifs."""
from __future__ import annotations

MAX_CONSECUTIVE_FAILURES = 3


class VoiceFallback:
    def __init__(self) -> None:
        self._consecutive_failures = 0
        self._disabled = False

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            self._disabled = True

    def record_success(self) -> None:
        self._consecutive_failures = 0

    @property
    def disabled(self) -> bool:
        return self._disabled

    def reset(self) -> None:
        self._consecutive_failures = 0
        self._disabled = False
```

### Intégration `app.py` (extrait pertinent)

Ajout d'un 5ème starter :
```python
cl.Starter(
    label="🔊 Brief vocal activé — Fiche LVMH",
    message="[VOICE=on] Donne-moi la fiche de LVMH",
),
```

Settings Chainlit :
```python
@cl.on_chat_start
async def on_start():
    await cl.ChatSettings([
        cl.input_widget.Switch(id="voice_brief", label="🔊 Brief vocal", initial=False),
        cl.input_widget.Select(
            id="voice",
            label="Voix",
            values=["Gaëlle", "Guillaume"],
            initial_index=0,
        ),
    ]).send()
```

Après chaque réponse, si toggle ON et pas disabled par fallback :
```python
script = await briefer.generate_brief(question=message.content, response=msg.content)
# Afficher transcription (WCAG)
await cl.Message(content=f"📻 **Brief audio** : _{script}_", author="Brief").send()
try:
    voice_id = settings.ELEVENLABS_VOICE_GAELLE if voice == "Gaëlle" else settings.ELEVENLABS_VOICE_GUILLAUME
    audio, cached = await tts.synthesize(script, voice_id)
    await cl.Audio(content=audio, mime="audio/mpeg", auto_play=True, display="inline").send()
    fallback.record_success()
except tts.ElevenLabsError as exc:
    fallback.record_failure()
    await cl.Message(content=f"🔇 Brief vocal indisponible : {type(exc).__name__}", author="Voice").send()
```

### Tests à produire

#### Unitaires briefer

```python
# tests/unit/test_S10_briefer.py
import pytest
from genial_agent.voice.briefer import generate_brief, MAX_WORDS


@pytest.mark.integration
async def test_brief_under_max_words():
    # Requires ANTHROPIC_API_KEY
    import os
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("no anthropic key")
    script = await generate_brief(
        question="Fiche LVMH",
        response="LVMH, SIREN 775670417, siège Paris, CA 94 Md€ en 2023.",
    )
    assert len(script.split()) <= MAX_WORDS
    # Ne doit pas contenir "775670417"
    assert "775670417" not in script
```

#### Unitaires TTS

```python
# tests/unit/test_S10_tts.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from genial_agent.voice import tts


def test_cache_key_stable():
    k1 = tts._cache_key("hello", "v1", "m1")
    k2 = tts._cache_key("hello", "v1", "m1")
    assert k1 == k2


def test_cache_key_changes_with_input():
    k1 = tts._cache_key("hello", "v1", "m1")
    k2 = tts._cache_key("world", "v1", "m1")
    assert k1 != k2


@pytest.mark.asyncio
async def test_map_error_401():
    resp = httpx.Response(status_code=401, text="bad key")
    err = tts._map_error(resp)
    assert isinstance(err, tts.AuthError)


@pytest.mark.asyncio
async def test_map_error_402():
    resp = httpx.Response(status_code=402, text="quota")
    err = tts._map_error(resp)
    assert isinstance(err, tts.QuotaExhausted)


@pytest.mark.asyncio
async def test_map_error_429():
    resp = httpx.Response(status_code=429, text="rl")
    err = tts._map_error(resp)
    assert isinstance(err, tts.RateLimited)


@pytest.mark.asyncio
async def test_map_error_500():
    resp = httpx.Response(status_code=500, text="oops")
    err = tts._map_error(resp)
    assert isinstance(err, tts.TransientError)
```

#### Fallback

```python
from genial_agent.voice.fallback import VoiceFallback


def test_fallback_disables_after_3_failures():
    fb = VoiceFallback()
    fb.record_failure()
    fb.record_failure()
    assert not fb.disabled
    fb.record_failure()
    assert fb.disabled


def test_fallback_resets_on_success():
    fb = VoiceFallback()
    fb.record_failure()
    fb.record_failure()
    fb.record_success()
    assert fb._consecutive_failures == 0
```

#### Intégration

```python
# tests/integration/test_S10_live.py
import os
import pytest
from genial_agent.voice import tts

pytestmark = pytest.mark.integration
SKIP = not os.getenv("ELEVENLABS_API_KEY")


@pytest.mark.skipif(SKIP, reason="no elevenlabs key")
async def test_gaelle_synthesize_real():
    voice_id = os.getenv("ELEVENLABS_VOICE_GAELLE", "tKaoyJLW05zqV0tIH9FD")
    audio, cached = await tts.synthesize(
        "Bonjour, ceci est un test de voix française.", voice_id
    )
    assert isinstance(audio, bytes) and len(audio) > 1000
    assert cached is False


@pytest.mark.skipif(SKIP, reason="no elevenlabs key")
async def test_cache_hit_second_call():
    voice_id = os.getenv("ELEVENLABS_VOICE_GAELLE", "tKaoyJLW05zqV0tIH9FD")
    text = "Test idempotence cache."
    audio1, cached1 = await tts.synthesize(text, voice_id)
    audio2, cached2 = await tts.synthesize(text, voice_id)
    assert cached1 is False
    assert cached2 is True
    assert audio1 == audio2
```

### Commit phase 2

`feat(S10): ElevenLabs voice brief with idempotence, retry, fallback`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] Le briefer ne voit **que** la sortie déjà validée par S05 (pas de
      tool calls bruts).
- [ ] Le script généré ne contient jamais de SIREN (test unitaire vert).
- [ ] Retry ne s'applique pas aux 401/402/422 (pas de spam d'une clé
      invalide).
- [ ] Cache hit bypasse bien l'appel réseau.
- [ ] Fallback `VoiceFallback` propage l'état via `cl.user_session`.
- [ ] Le toggle est OFF par défaut (vérif UI).
- [ ] Transcription toujours affichée sous l'audio (WCAG).
- [ ] Pas de fuite de `ELEVENLABS_API_KEY` dans les logs.

### Commit phase 3

`review(S10): approved`

---

## ✅ Critères d'acceptation

- [ ] Toggle ON génère un audio audible avec Gaëlle et/ou Guillaume.
- [ ] Script ≤ 100 mots, sans SIREN dans l'audio.
- [ ] Transcription affichée sous chaque audio.
- [ ] Cache hit : 2 appels identiques rapprochés = 1 appel réseau.
- [ ] 401/402 → toggle auto-désactivé + message clair.
- [ ] Fallback après 3 échecs consécutifs.
- [ ] Tests unitaires verts.
- [ ] Tests d'intégration verts avec clé réelle (ou skip documenté).
- [ ] `gitleaks` clean.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + tests verts.
- [ ] Phase 3 approuvée.
- [ ] Scénario 7 ajouté au Loom.
- [ ] Ligne S10 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push effectué.
