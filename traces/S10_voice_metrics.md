# S10 voice mode — métriques (étape 6 phase 2)

> **Statut** : skeleton créé en phase 2 dev. À remplir lors du POC live
> une fois l'Eleven Agent créé côté dashboard ElevenLabs et le secret
> propagé Workspace → Railway → `.env`. Cf. story S10 §"Étape 6 — 🔴
> Boucle validation observée".

## Targets phase 1 (rappel)

| Composante | Cible | Mesure |
|---|---|---|
| ASR ElevenLabs FR (round-trip) | 600-800 ms | _à mesurer_ |
| Custom LLM (Anthropic via /v1/chat/completions) | 2-5 s sur U1 | _à mesurer_ |
| TTS ElevenLabs streaming (time-to-first-byte audio) | 300-500 ms | _à mesurer_ |
| **Cumul end-to-end U1** | **3-6 s** | _à mesurer_ |
| **Cumul end-to-end U3** | **viable avec narration tool steps** | _à mesurer_ |

## Métriques à reporter (par scénario)

| Scénario | ASR ms | Custom LLM ms | TTS first-byte ms | Total end-to-end | Narration émise ? | Notes |
|---|---:|---:|---:|---:|---|---|
| U1 fiche LVMH (run 1) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| U1 fiche LVMH (run 2) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| U1 fiche LVMH (run 3) | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| U2 mandats Arnault | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | |
| U3 Carrefour vs Casino | _TBD_ | _TBD_ | _TBD_ | _TBD_ | _TBD_ | Tester narration sur tool steps |
| Cas erreur (secret KO) | n/a | n/a | n/a | n/a | n/a | Vérifier widget reste muet, chat texte OK |
| Interruption user | _TBD_ | _TBD_ | n/a | n/a | _TBD_ | Vérifier `voice_cancelled_total` ↑ + `state.lock` libéré |

## Coût Eleven Agents observé

- Pricing confirmé phase 1 : 10 ¢/min Creator/Pro, 8 ¢/min Business annuel.
- Tier `growing_business` : couvre TTS classique (5,9 M chars/mois),
  minutes Eleven Agents en usage-based **à vérifier dans Usage**.
- Cumul minutes consommées sur le POC : _TBD_.
- Coût total estimé : _TBD $_.

## Observabilité backend (snapshot `/stats` post-POC)

À mesurer après ~10 sessions voice :

```json
{
  "voice_sessions_total": _TBD_,
  "voice_custom_llm_calls": _TBD_,
  "voice_chars_tts": _TBD_,
  "voice_narration_chunks_emitted": _TBD_,
  "voice_cancelled_total": _TBD_
}
```

## Décisions post-POC

À documenter ici :

- Si latence U1 > 8 s médian → **fallback Plan B (brief vocal v1)**
  (cf. story §"Plan B").
- Si turn-taking Patient trop lent → tester `Normal` ou `Eager`.
- Si narration tool steps inaudible (TTS Eleven coalesce) → ajuster
  les espaces / ponctuation côté `narrate.py`.
