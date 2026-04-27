# S10 voice mode — métriques live (POC 2026-04-27)

> **Statut** : POC live exécuté 2026-04-27 contre Railway prod via
> `simulate-conversation` API ElevenLabs + test audio user (Mac
> Safari). Cf. story S10 §"Phase 2.5 — Hotfixes POC live".

## Cibles initiales (story phase 1) vs réalité

| Composante | Cible phase 1 | Mesuré live | Verdict |
|---|---:|---:|---|
| ASR ElevenLabs FR (round-trip) | 600-800 ms | non instrumenté côté nous | Eleven gère, perceptu OK après bump widget 0.5.4 → 0.11.6 |
| Custom LLM Pass 1 (Anthropic + tool calls) | 2-5 s sur U1 | 1.9-3.95 s narration tool steps + ~10 s total LVMH | OK U1, lourd U3 |
| Pass 2 reformulateur Haiku (latence ajoutée) | n/a (pas prévu en phase 1) | 1.5-2 s sur conversationnel, 2-3 s sur LVMH | tradeoff assumé pour qualité voice |
| TTS Eleven streaming first-byte | 300-500 ms | non instrumenté ; perçu OK avec `optimize_streaming_latency=0` | n/a |
| **Cumul end-to-end U1 simple** | **3-6 s** | **4.3 s** (avec skip B2 sur conversationnel) | ✅ |
| **Cumul end-to-end U3 (LVMH)** | viable avec narration | **12.0 s** (vs 16.5 s avant hotfixes A4+B1+B2) | ✅ acceptable démo |

## Mesures live SSE direct vers `/v1/chat/completions` prod

Mesuré via `curl` direct (sans audio TTS, juste SSE), depuis WSL en
France, le 2026-04-27 vers ~10:30 UTC.

### Test 1 — "Salut, comment vas-tu ?" (skip B2 actif)

| Chunk | t_total | Δms | chars | content (start) |
|---|---:|---:|---:|---|
| Role chunk | 0 ms | 0 | 0 | `delta.role=assistant` |
| Main response | 3850 ms | 3853 | 8 | `Salut !` |
| Cont. | 4040 ms | 187 | 31 | `Ça va bien, merci de demander.` |
| Cont. | 4280 ms | 241 | 198 | `Je suis là pour t'aider…` |
| `[DONE]` | 4300 ms | 20 | 0 | — |

- TTFT premier contenu : **3.85 s** (cohérent avec voice_system_prompt v2 ~3 KB qui slow Haiku 1er token, pas de cache hit sur session voice éphémère)
- Total : **4.3 s**
- Skip B2 confirmé : pas d'appel reformulateur (logs : `voice_reformulator_skipped reason=already_voice_friendly main_chars=237`)

### Test 2 — "Tu peux me parler de LVMH ?" (Pass 2 + narration)

| Chunk | t_total | Δms | chars | content (start) |
|---|---:|---:|---:|---|
| Role chunk | 0 ms | 0 | 0 | `delta.role=assistant` |
| Narration | 1900 ms | 1896 | 21 | `Je cherche le SIREN…` |
| Narration | 3950 ms | 2053 | 30 | `Je regarde les chiffres clés…` |
| Reformulé | 11250 ms | 7305 | 90 | `LVMH Moët Hennessy Louis Vuitton est une société européenne basée à Paris…` |
| Reformulé | 11700 ms | 440 | 250 | `Selon Pappers, au niveau du siège social, le chiffre d'affaires atteint environ…` |
| Reformulé | 11910 ms | 214 | 58 | `Le capital social s'élève à environ 150 millions d'euros.` |
| Reformulé | 12040 ms | 130 | 136 | `Pour une vision complète du groupe avec toutes ses marques et filiales, je te re…` |
| `[DONE]` | 12060 ms | 20 | 0 | — |

- Pass 1 narration émise live à 1.9 s + 3.95 s ✅ (mitigation latence)
- Silence 7.3 s entre dernière narration et 1er chunk reformulateur (Pass 1 main LLM continue de générer du texte bufferé sans event narratif)
- Pass 2 reformulateur émet en chunks de phrases complètes (sentence_buffer A4) à 130-440 ms d'écart
- `[DONE]` flushé immédiatement (hotfix C : pas de pause `aclose()` finale)
- Inter-chunk médian : **327 ms** (vs 344 ms avant, mais sur **phrases complètes** désormais)

## Configuration agent ElevenLabs finale

Cf. story S10-voice-brief.md §"Phase 2.5 — Configuration finale agent
ElevenLabs (prod)" pour le payload complet.

Key params :
- Voice : Marine - Premium Conversational AI (`6FXyooAOTqUK8m2HWm32`)
- Model : `eleven_turbo_v2_5`
- `optimize_streaming_latency: 1` (compromis TTFT/jitter — PATCH A=0 testé live puis reverted le 2026-04-27 : pas d'amélioration audible + latence ressentie plus lente)
- `stability: 0.75` + `similarity_boost: 0.8`
- `soft_timeout_config: timeout_seconds=3.0` + filler statique FR

## Coût Eleven Agents observé

- Conversations cumulées POC : **7 sessions** (cf. logs API
  `GET /v1/convai/conversations?agent_id=…`).
- Durée moyenne : ~30-100 s par conversation.
- Cumul ~6 minutes consommées le 2026-04-27.
- Tier `growing_business` : minutes Eleven Agents en usage-based.
- Coût estimé POC complet : **< 1 $**.

## Observabilité backend (snapshot `/stats` post-POC)

```json
{
  "voice_sessions_total": 7+,
  "voice_custom_llm_calls": 7+,
  "voice_chars_tts": ~5000,
  "voice_narration_chunks_emitted": 8-12,
  "voice_cancelled_total": 1+ (test interruption + curl head -8)
}
```

## Décisions post-POC

- ✅ **Latence U1 < 8 s confirmée** (4.3 s mesuré) → on **garde le voice mode v2**, Plan B (brief vocal v1) **non déclenché**.
- ✅ Turn eagerness `normal` retenu (vs `patient` initial — moins de pauses gênantes).
- ⚠ Jitter résiduel acceptable pour démo (cf. story §"Phase 2.5 — Jitter résiduel"). Si rework : bump `model_id: turbo_v2_5 → multilingual_v2`.
- ⚠ Latence Pass 1 + Pass 2 séquentiels reste un coût (12 s sur U3). **Next-step possible** : reformulateur en streaming overlapped (lance Haiku dès la 1ère phrase complète du main LLM, pas attendre `routing_done`). +2 h dev, gain ~1.5-2 s.

## Conversations live archivées

Les transcripts complets restent disponibles via API :

```bash
curl -sS -H "xi-api-key: $ELEVENLABS_API_KEY" \
  "https://api.elevenlabs.io/v1/convai/conversations?agent_id=agent_5301kq6me9p9f79s3dvsx4r0afhq&page_size=20"

# Détail d'une conv (transcript + analysis) :
curl -sS -H "xi-api-key: $ELEVENLABS_API_KEY" \
  "https://api.elevenlabs.io/v1/convai/conversations/{conversation_id}"
```

## Hotfixes appliqués (chronologique 2026-04-27)

| Heure | Action | Commit / API |
|---|---|---|
| ~04:50 | Push initial S10 phase 2 (`feat(S10)`) | `112e8fe` |
| ~04:51 | doc deployment.md gotchas POC live | `44a5333` |
| ~04:52 | Setup secret + agent prod ElevenLabs | API |
| ~04:55 | Bump widget 0.5.4 → 0.11.6 (ASR muet) | `42db515` |
| ~05:05 | Voix Gaëlle → Marine + dark mode + textes FR | API (× 3 PATCHes) |
| ~05:15 | VOICE_SUFFIX v2 OVERRIDE | `cef82f4` |
| ~05:30 | Pipeline 2-passes + done-before-aclose + strip Markdown | `114fa55` |
| ~05:50 | A1+A2+A3+B3 (TTS turbo, stability, latency, soft timeout) | API (× 1 PATCH) |
| ~06:00 | A4+B1+B2 (sentence_buffer + fillers + skip) | `b432e66` |
| ~06:10 | PATCH A : `optimize_streaming_latency=0` (anti-jitter test) | API |
| ~06:30 | **Revert PATCH A → 1** (test live : pas d'effet audible + ressenti plus lent) | API |
