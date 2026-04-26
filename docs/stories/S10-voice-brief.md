# S10 — 🎯 Stretch : Voice Mode conversationnel via ElevenLabs Agents

> **Statut** : ⏸ bloqué par gating
> **Durée estimée** : 4-6 h dev (POC) + 1 h gating + 1 h doc
> **Parallélisable avec** : —
>
> **Pivot 2026-04-26** : la version initiale de cette story prévoyait un
> **simple TTS** de la réponse texte (un brief radio de 30-40 s lu par
> Gaëlle/Guillaume après la réponse). Sur recherche utilisateur, on
> repivote vers un **vrai voice mode conversationnel** style ChatGPT
> Voice : l'utilisateur parle, l'agent comprend (ASR), enchaîne ses
> tools Pappers, répond oralement (TTS) en temps réel, sans bouton
> d'enregistrement manuel. Le cahier §19 sera amendé en cohérence
> **uniquement après** validation phase 1 par l'agent d'élicitation
> (les hypothèses techniques 2026 doivent être vérifiées avant de
> figer la spec produit).

---

## 📍 Contexte

ElevenLabs a sorti **Eleven Agents** (GA 2026) — une stack
conversationnelle qui packagee :

- **ASR** (speech-to-text fine-tuné multilingue),
- **Turn-taking model propriétaire** (VAD + analyse prosodie /
  micro-pauses) qui détecte fin de parole, hésitations, interruptions
  — **pas besoin de bouton d'enregistrement manuel**,
- **TTS** low-latency sur 5k+ voix, 70+ langues,
- **Custom LLM** branchable via endpoint OpenAI-compatible SSE
  (`POST /v1/chat/completions`) → on peut **brancher notre agent
  Genial existant** sans rien changer à sa logique cerveau (Claude
  Haiku/Sonnet, MCP Pappers, payload vault, caps),
- **Soft timeout** natif : si le custom LLM tarde, ElevenLabs
  prononce un filler généré contextuellement ("Hmm…", "Je vois…")
  après un délai configurable (défaut 3 s), 1 fois par tour, pour
  éviter le silence gênant pendant que Pappers répond.

C'est une opportunité pour livrer un **effet waouh disproportionné**
sur l'évaluateur Fabien : agent vocal naturel, fluide, qui parle
français, sans coût d'orchestration custom. Le vrai différenciant
2026 vs un simple TTS de réponse.

### Pourquoi on abandonne la version "brief vocal" originale

| Aspect | Brief vocal (S10 v1) | Voice mode (S10 v2) |
|---|---|---|
| UX | TTS post-réponse, lecture passive | Conversation duplex, naturelle |
| Effort | ~2 h dev | ~5 h dev |
| Différentiant 2026 | faible (tout le monde a TTS) | élevé (agent voice ≠ chatbot vocal) |
| Risque latence | nul (off-path) | réel sur U3 (mitigation plus bas) |
| Signal recruteur | "il sait câbler du TTS" | "il sait orchestrer une stack agent voice 2026" |

Le brief vocal reste documenté en **fallback de secours** (cf. §"Plan
B") si la phase 1 d'élicitation révèle que le voice mode n'est pas
faisable dans le budget temps.

Sources de vérité :

- [`docs/cahier-des-charges.md`](../cahier-des-charges.md) §19 (à
  amender post-élicitation phase 1).
- ElevenLabs docs : https://elevenlabs.io/docs/eleven-agents/overview
  (vérifier versions à la date de phase 1).
- Notre agent existant
  [`src/genial_agent/agent.py`](../../src/genial_agent/agent.py)
  expose déjà du streaming Anthropic — l'adapter en SSE
  OpenAI-compatible est un wrapper léger (~50-80 lignes).

---

## 🔒 Prérequis (gating strict)

Aucun écart toléré. Cocher avant de démarrer phase 1 :

- [ ] **S09.7 mergée et stable** (commit review approved).
- [ ] URL Railway prod stable, healthcheck UptimeRobot vert.
- [ ] 3 tests officiels Pappers OK en live.
- [ ] Pack adversarial S09 ≥ 9/10 effectif.
- [ ] **Latence U1/U2 mesurée < 10 s** sur les 3 derniers runs live
      (pré-requis dur — voir §"Risques" R23 plus bas).
- [ ] Empty state + starters + SIREN cliquables opérationnels.
- [ ] `EVALUATION.md` rédigé.
- [ ] **Bug crédits prewarm** (cf. capture 2026-04-26 20:32 — appels
      `sirenisateur` parasites au boot de chaque chat) **investigué et
      fixé**, sinon le voice mode l'amplifiera (chaque session voice
      = 1 nouveau `on_chat_start`).

## 🔑 Inputs utilisateur requis

- [x] `ELEVENLABS_API_KEY` fournie et active (probe 2026-04-24,
      tier `growing_business`, quota mensuel 5 922 075 chars).
- [x] `ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD`.
- [x] `ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD`.
- [ ] **Solde crédits Pappers ≥ 100** au moment du POC voice mode
      (chaque test live consomme des crédits agent normaux).
- [ ] Création d'un **Eleven Agent dans le dashboard ElevenLabs**
      (l'utilisateur doit valider qu'on a les droits ; tier
      `growing_business` couvre normalement le produit Eleven Agents
      mais à confirmer en phase 1).
- [ ] `ENABLE_VOICE_MODE=true` dans Railway + `.env` local **après**
      merge S10 + gating respecté. Aujourd'hui valeur cible `false`.

> Renommé : `ENABLE_VOICE_BRIEF` (S10 v1) → `ENABLE_VOICE_MODE`
> (S10 v2) pour refléter le nouveau scope. Nettoyer l'ancien nom dans
> `.env.example`, README, `docs/cahier-des-charges.md` §19.6.

---

## 🎯 Scope

### Dans le scope

1. **Endpoint OpenAI-compatible SSE** (`POST /v1/chat/completions`)
   exposé par notre app Chainlit/FastAPI, qui wrappe l'agent
   `run_guarded_turn` existant. C'est le pont entre Eleven Agent et
   notre cerveau Genial.
2. **Streaming narratif des tool steps** : pendant que l'agent
   enchaîne ses appels Pappers, le wrapper émet des chunks SSE
   "voice-friendly" ("Je consulte la fiche Carrefour…", "Je regarde
   les comptes 2023…") pour que ElevenLabs ait du texte à TTS-er en
   continu. Mitigation latence U3.
3. **Prompt voice-friendly** appliqué au LLM pour la réponse finale :
   pas de SIREN à voix haute (les énoncer cassent l'oreille — cf.
   cahier §19.7), arrondir les chiffres, style narratif fluide. Soit
   on injecte un system prompt quand `voice_mode=on`, soit on insère
   un Haiku reformulateur en queue de pipeline (à arbitrer en
   phase 1 selon coût latence + qualité observée).
4. **Eleven Agent configuré** pointant sur notre endpoint custom LLM,
   voix Gaëlle (défaut) / Guillaume (sélecteur). Soft timeout configuré
   à 3 s avec fallback statique FR.
5. **Widget JS embeddable** intégré dans Chainlit (via `custom_html`
   ou `custom_js` en `.chainlit/config.toml`) — bouton micro
   discret en bas du chat, déclenche la session voice. Le chat texte
   Chainlit reste opérationnel en parallèle (l'utilisateur peut
   alterner texte / voix dans la même session).
6. **Feature flag global** `ENABLE_VOICE_MODE` — désactivable à chaud
   via env var Railway sans redeploy.
7. **Observabilité dédiée** : logs structurés sur chaque session voice
   (durée, mots ASR transcrits, mots TTS générés, custom LLM tokens,
   coût estimé), agrégés dans `/stats`.

### Hors scope (explicite)

- **Knowledge base RAG ElevenLabs** : on garde MCP Pappers côté
  agent. Pas de duplication.
- **Système de tools côté ElevenLabs** (webhooks, transfer_to_agent) :
  notre custom LLM gère tout, ElevenLabs voit du texte.
- **Telephony / batch outbound** : feature ElevenLabs hors démo.
- **Voice cloning custom** : on utilise les voix Gaëlle / Guillaume
  déjà choisies (cahier §19.5).
- **Persistance audio cross-session** : chaque session voice est
  éphémère côté navigateur, pas de "rejouer ma dernière conversation
  vocale". (Le thread texte sous-jacent reste persisté côté Chainlit
  data layer comme pour une conv normale.)
- **Anti-abus voice** (cap minutes/IP, rate limit) : hors scope démo,
  cohérent avec décision S09.7 §"Hors scope explicite".

---

## 🏗 Architecture cible (à valider phase 1)

```
[mic navigateur]
    ↓
[Widget JS ElevenLabs] (embed Chainlit)
    ├── ASR ElevenLabs → texte transcrit
    ├── Turn-taking propriétaire (détecte fin de parole)
    └── envoie → POST /v1/chat/completions (SSE) sur notre app
                          ↓
            [Adapter OpenAI ← → Anthropic]
            (src/genial_agent/voice/openai_adapter.py)
                          ↓
            [run_guarded_turn] ← agent existant inchangé
                          ↓ stream events
            ┌─────────────┴─────────────┐
            ↓                           ↓
    tool_use events             text events finaux
            ↓                           ↓
    chunk SSE narratif          chunk SSE réponse voice-friendly
    "Je consulte X…"            (post-process ou prompt système)
                          ↓
            renvoyé à Eleven Agent en SSE
                          ↓
            [TTS ElevenLabs] (Gaëlle/Guillaume)
                          ↓
[haut-parleur navigateur]

Si pause LLM > 3 s → ElevenLabs déclenche un filler natif
(soft timeout) du genre "Hmm…" → reprend dès qu'un chunk arrive.
```

**Principes** :
- Notre **agent reste 100 % inchangé** côté logique (tools, MCP, vault,
  caps, routing Haiku/Sonnet). Voice mode est une couche I/O.
- Le **cap dur 60 s wall-clock** côté agent reste actif — si U3 dépasse
  60 s, le cap-as-UX-event de S09.7 se déclenche (utilisateur peut
  dire "continue" oralement, qui passera par ASR → "continue" texte).
- Le **streaming narratif** est généré côté wrapper (pas par le LLM
  cerveau) à partir des events `tool_use` que `run_guarded_turn` émet
  déjà — pas de second LLM nécessaire.

---

## 🧭 Phase 1 — Elicitation Agent

L'agent d'élicitation S10 doit **valider en ligne** les hypothèses
suivantes avant de figer la phase 2 :

### Recherches obligatoires

- [ ] **Eleven Agents — pricing 2026** : facturation en minutes ?
      caractères ? combiné ? Notre tier `growing_business` couvre-t-il
      Eleven Agents ou est-ce un add-on ? Cap journalier raisonnable
      pour une démo week-end (estimer : ~30 min de voice cumulées sur
      2 j × Fabien + équipe + nous).
- [ ] **Custom LLM endpoint — spec exacte 2026** :
      - Format `/v1/chat/completions` SSE (chunks `data: {json}\n\n`
        terminés par `data: [DONE]\n\n`) ou nouveau format
        `/v1/responses` (events typés `response.output_text.delta`) ?
      - L'agent ElevenLabs envoie-t-il les `tools` ? (réponse
        connue : oui pour les system tools type `end_call`, mais
        on peut les ignorer côté custom LLM sans erreur ?)
      - Comment authentifie-t-il ses appels vers notre endpoint ?
        (header custom, signature HMAC, IP allowlist ?) Verrouiller
        l'endpoint pour qu'aucun acteur externe ne puisse cramer nos
        crédits Pappers en l'appelant directement.
      - Buffer words / soft timeout : doc exacte 2026, valeur par
        défaut, format du fallback statique.
- [ ] **Widget JS embeddable — intégration Chainlit** :
      - Comment l'embed (`custom_html` ? `custom_js` qui inject un
        `<script>` ? iframe ?) ?
      - Coexiste-t-il avec le chat texte Chainlit dans la même page
        sans conflit CSS / WebSocket ?
      - Peut-on customiser le bouton micro (position, couleurs HSL
        Genial #6040C0) ?
      - Mode "push to talk" (bouton maintenu) **vs** mode auto
        (turn-taking détecte fin de parole) — supporte-t-il les deux ?
- [ ] **SDK Python ElevenLabs Agents** (si existe en 2026) — version,
      classe `Agent`, méthodes pertinentes. Sinon : appels REST directs
      via `httpx` (pattern S10 v1 réutilisable).
- [ ] **Compatibilité Chainlit data layer** : si l'utilisateur utilise
      voice puis bascule en texte dans la même session, le thread
      Chainlit reflète-t-il les 2 modes ? Ou bien voice = thread séparé ?
- [ ] **Latence end-to-end mesurée** sur cas U1 (fiche LVMH simple) :
      ASR + custom LLM endpoint + TTS = combien en cumulé ? Acceptable
      < 8 s pour parler de "fluide". Au-delà, le voice mode dégrade
      l'UX par rapport au texte.

### Points à résoudre (décisions phase 1)

- [ ] **Prompt voice-friendly** : injection system prompt vs. Haiku
      reformulateur en queue ?
      - Injection : -0 latence, peut-être moins de qualité si l'agent
        en plein chaînage tool-use ne peut pas "refactor" sa réponse.
      - Reformulateur Haiku : +400-600 ms latence, qualité prévisible
        (équivalent à ce que faisait `briefer.py` du S10 v1).
      - **Critère décision** : tester les 2 variantes en local sur
        cas U1 (fiche LVMH) et choisir selon (a) qualité orale
        subjective, (b) latence cumulée.
- [ ] **Streaming narratif tool steps** : taggé "thinking aloud" en
      français, neutre.
      - Pattern proposé : sur chaque `tool_use` event de
        `run_guarded_turn`, émettre un chunk SSE `"Je consulte
        {tool_name}…"` avec mapping minimal (`sirenisateur` →
        "Je cherche le SIREN…", `comptes-entreprise` → "Je regarde
        les comptes…", etc.). Mapping ≤ 6 entrées, pas de hardcoding
        d'entité (cohérent avec philosophie S09.7 "agent adaptable").
      - Trade-off : trop verbeux = robot, trop discret = silence
        ressenti.
- [ ] **Sécurisation de l'endpoint custom LLM** : si ElevenLabs
      authentifie via signature, on whitelist côté `/v1/chat/completions` ;
      si pas d'auth native, on ajoute un token partagé en header
      (env `ELEVEN_AGENT_SHARED_TOKEN`) que ElevenLabs envoie à
      chaque appel. Bloquer toute requête sans token (sinon n'importe
      qui peut cramer nos crédits Pappers + Anthropic en spam-callant
      `/v1/chat/completions`).
- [ ] **Cas d'erreur produit** :
      - ElevenLabs Agent KO → fallback texte automatique ? bandeau UI ?
      - Custom LLM endpoint timeout → ElevenLabs déclenche soft
        timeout → puis quoi si on ne revient jamais ?
      - User parle pendant que l'agent répond (interruption native
        ElevenLabs) → comment annuler `run_guarded_turn` en cours
        proprement ? (cancel context Anthropic, libérer crédits non
        consommés ?)

### Mise à jour cahier post-phase 1

Une fois ces points résolus, **amender `docs/cahier-des-charges.md`
§19** :
- Renommer §19 "brief vocal" → "voice mode conversationnel".
- Mettre à jour §19.2 (concept), §19.3 (architecture), §19.6
  (config — `ENABLE_VOICE_MODE`), §19.7 (prompts — décision
  injection vs reformulateur), §19.10 (gain démo), §19.12
  (robustesse intégration — soft timeout, sécurisation endpoint).
- Garder §19.5 (voix Gaëlle/Guillaume retenues, IDs inchangés).
- §19.9 coût recalculé selon pricing minutes Eleven Agents.

### Commit phase 1

`story(S10): refine — voice mode v2 (Eleven Agents + custom LLM endpoint)`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier (figés post-phase 1)

| Action | Fichier | Rôle |
|---|---|---|
| ➕ | `src/genial_agent/voice/__init__.py` | namespace |
| ➕ | `src/genial_agent/voice/openai_adapter.py` | endpoint `/v1/chat/completions` SSE qui wrap `run_guarded_turn` |
| ➕ | `src/genial_agent/voice/narrate.py` | mapping `tool_name` → phrase narrative ("Je consulte X…"), neutre, ≤ 6 entrées |
| ➕ | `src/genial_agent/voice/voice_prompt.py` | system prompt voice-friendly (variante runtime quand `voice_mode=on`) ou wrapper Haiku reformulateur (selon décision phase 1) |
| ➕ | `src/genial_agent/voice/security.py` | guard partagé `verify_eleven_request` (token + IP allowlist optionnelle) |
| ✏️ | `src/genial_agent/app.py` | (a) montage du endpoint `/v1/chat/completions` ; (b) injection du widget JS Eleven Agents via `custom_js` ou `custom_html` ; (c) toggle settings voix Gaëlle/Guillaume |
| ✏️ | `src/genial_agent/config.py` | `ENABLE_VOICE_MODE` + `ELEVEN_AGENT_ID` + `ELEVEN_AGENT_SHARED_TOKEN` |
| ✏️ | `.env.example` | documentation des nouvelles vars + suppression de `ENABLE_VOICE_BRIEF` |
| ✏️ | `.chainlit/config.toml` | `custom_js` étendu pour inclure le widget Eleven |
| ✏️ | `public/eleven-widget.js` (ou via CDN ElevenLabs) | bootstrap du widget côté navigateur |
| ✏️ | `src/genial_agent/observability/stats.py` | compteurs `voice_sessions_total`, `voice_minutes_cumulated`, `voice_custom_llm_calls`, `voice_chars_tts` |
| ✏️ | `docs/cahier-des-charges.md` | §19 amendé (cf. phase 1) |
| ✏️ | `docs/deployment.md` | section §3 ter — création de l'Eleven Agent + binding endpoint custom LLM + env vars `ENABLE_VOICE_MODE`, `ELEVEN_AGENT_ID`, `ELEVEN_AGENT_SHARED_TOKEN` |
| ➕ | `tests/unit/test_S10_openai_adapter.py` | tests structurels du wrapper SSE (format chunks, conversion Anthropic→OpenAI, narration tool_use) |
| ➕ | `tests/unit/test_S10_narrate.py` | tests mapping tool_name → phrases ; vérifier zéro hardcoding entité (grep LVMH/Carrefour) |
| ➕ | `tests/unit/test_S10_security.py` | tests guard endpoint (token absent → 401, token bidon → 401, token correct → 200) |
| ➕ | `tests/integration/test_S10_voice_e2e.py` | live-only `@pytest.mark.integration` : crée une session Eleven Agent, simule un input audio "Donne-moi LVMH", attend la réponse audio, parse sa transcription, assert SIREN cité |

### Étapes (séquentielles, ordre figé phase 1)

**Étape 0 — Setup**
- Créer un Eleven Agent dans le dashboard ElevenLabs.
- Pointer son LLM custom sur `https://<railway-domain>/v1/chat/completions`.
- Récupérer `agent_id` + `shared_token` (généré côté nous).

**Étape 1 — Endpoint adapter** (~1.5 h)
- Tests unit `test_S10_openai_adapter.py` (TDD).
- Implémenter `openai_adapter.py` qui :
  - reçoit `{messages, model, stream: true}` au format OpenAI Chat,
  - convertit `messages` en historique Anthropic compatible
    `ConversationState`,
  - drive `run_guarded_turn` (notre agent existant, sans modif),
  - convertit chaque event Anthropic en chunk SSE OpenAI :
    - `text` event → `data: {"choices": [{"delta": {"content": "..."}}]}`,
    - `tool_use` event → chunk SSE narratif via `narrate.py`,
    - `routing_done` final → chunk fin + `data: [DONE]\n\n`,
  - applique le **prompt voice-friendly** (selon décision phase 1).
- Monter sur l'app Chainlit via `cl.user_session` ou route FastAPI
  annexe (Chainlit ≥ 2.10 expose `app.router` FastAPI sous-jacent).

**Étape 2 — Sécurisation endpoint** (~30 min)
- `security.py` : middleware qui vérifie `Authorization: Bearer
  <ELEVEN_AGENT_SHARED_TOKEN>`. Rejeter 401 sans log de la valeur.
- Tests unit `test_S10_security.py`.

**Étape 3 — Widget UI** (~1 h)
- Config Eleven Agent dashboard : voix Gaëlle (défaut), soft timeout
  3 s, fallback statique "Un moment, je consulte les données…".
- Embed widget dans Chainlit via `custom_js` ou `custom_html`.
- Style : bouton micro en bas du chat, couleurs Genial (HSL
  `255 50% 50%`).

**Étape 4 — Streaming narratif tool steps** (~30 min)
- `narrate.py` mapping ≤ 6 entrées, FR neutre.
- Wiring dans `openai_adapter.py` au flush de chaque `tool_use` event.
- Tests unit `test_S10_narrate.py`.

**Étape 5 — Observabilité** (~30 min)
- Compteurs `stats.py`.
- Forwarding via `/stats` endpoint (cohérent S07).

**Étape 6 — 🔴 Boucle validation observée** (~1 h)
- Lancer le Railway prod.
- Tester live :
  - U1 fiche LVMH (cible : voice mode fluide < 8 s end-to-end).
  - U2 mandats Arnault (cible : fluide).
  - U3 Carrefour vs Casino (cible : viable avec narration tool steps,
    soft timeout sur les pauses ; échec acceptable si > 60 s — cap
    dur agent toujours actif).
  - Cas erreur : couper l'API key Eleven, vérifier fallback texte.
  - Test interruption : parler par-dessus la réponse agent, vérifier
    que `run_guarded_turn` est cancellé proprement.
- Mesurer : latence ASR, latence custom LLM, latence TTS, durée
  totale ; reporter dans `traces/S10_voice_metrics.md`.

**Étape 7 — Doc + commit final** (~30 min)
- Update cahier §19 (selon phase 1).
- Update `docs/deployment.md` §3 ter (Eleven Agent setup).
- Update `README.md` (section voice mode + lien doc).
- Update `docs/stories/README.md` ligne S10 → 🟡 dev done.
- Commit final phase 2.

### Commit final phase 2

`feat(S10): voice mode conversationnel — Eleven Agents + custom LLM endpoint + narration tool steps`

---

## 🔍 Phase 3 — Review Agent

Check-list spécifique :

- [ ] Endpoint `/v1/chat/completions` répond uniquement avec
      `Authorization` valide (test 401 sur token absent / bidon).
- [ ] Pas de fuite de secret (token Eleven Agent, MCP key) dans les
      logs ou le widget JS côté client.
- [ ] L'agent reste **strictement inchangé** côté logique : `agent.py`,
      `mcp_pappers.py`, `payload_vault.py`, `routing.py`,
      `guardrails/*.py` ne sont pas touchés (grep diff).
- [ ] Aucun **hardcoding de logique métier** (LVMH, Carrefour, etc.)
      dans `narrate.py`, `voice_prompt.py`, ni dans le system prompt
      voice-friendly.
- [ ] Cap dur `60 s wall-clock` reste actif, le voice mode n'introduit
      pas de bypass.
- [ ] Pack adversarial S09 **maintenu** (voice mode hors path
      n'affecte pas T1-T10).
- [ ] Pack golden G1-G5 **maintenu** en mode texte.
- [ ] Latence U1 voice mode < 8 s médian sur 5 runs live.
- [ ] Si voice mode KO → fallback texte fonctionne sans crash.
- [ ] Feature flag `ENABLE_VOICE_MODE=false` désactive complètement
      le widget et le endpoint (ne pas laisser une route ouverte
      "désactivée").
- [ ] Coût Eleven Agents mesuré phase 2 dev documenté.

Sortie attendue : `review(S10): approved` ou `docs/stories/reviews/S10-rework.md`.

---

## ⚠️ Risques spécifiques

| # | Risque | Mitigation |
|---|---|---|
| R23 | Latence U3 ≥ 60 s tue le voice mode | Streaming narratif tool steps + soft timeout natif ElevenLabs ; documenter en `EVALUATION.md` que voice mode est optimal sur U1/U2 |
| R24 | Endpoint custom LLM exposé sans auth → spam crédits | Token partagé Bearer + IP allowlist optionnelle |
| R25 | Coût Eleven Agents inattendu (pricing minutes en 2026) | Cap minutes/jour côté config Eleven dashboard ; feature flag global désactivable à chaud |
| R26 | Interruption utilisateur ne cancel pas `run_guarded_turn` → orphan tools en cours | Intégrer `asyncio.Task.cancel()` dans le adapter, libérer crédits Pappers non consommés (best-effort) |
| R27 | Widget Eleven incompatible avec CSS Genial / Chainlit | Tester en dev local avant push prod ; fallback : iframe isolée |
| R28 | Auto-play audio bloqué Chrome au 1er chargement | L'utilisateur doit cliquer le bouton micro pour démarrer (interaction utilisateur explicite → autoplay autorisé) |
| R29 | ASR français de qualité variable selon accent / bruit | Tester avec 2-3 accents FR différents en phase 1 ; si KO, fallback push-to-talk |
| R30 | Bug crédits prewarm `on_chat_start` (cf. capture 2026-04-26) amplifié par voice (chaque session voice = nouveau on_chat_start) | **Bloqueur prérequis** : fixer avant de démarrer S10 ; sinon chaque session voice = 4 sirenisateurs cramés |

---

## 💰 Coût estimé pour le week-end

À recalculer en phase 1 selon pricing 2026. Estimation grossière :

- Eleven Agents : ~$0.10-0.30 / minute selon tier (à confirmer).
- Week-end avec 30 min cumulées : **~$3-9**.
- Anthropic custom LLM (Haiku/Sonnet via notre agent) : déjà
  budgeté côté agent normal, voice mode ne double pas la conso.
- Pappers : aucun surcoût direct (mêmes appels qu'en mode texte) **sauf
  si le bug prewarm n'est pas fixé**.

Acceptable si pricing confirmé en phase 1.

---

## 🛟 Plan B — fallback brief vocal v1

Si la phase 1 d'élicitation révèle un blocker dur (pricing
inacceptable, custom LLM endpoint instable, widget incompatible
Chainlit, latence ASR > 1.5 s incompressible) :

**Repli sur la version originale S10 v1** (TTS post-réponse, briefer
Haiku, lecteur audio inline). L'effort est connu (~2 h dev), le scope
documenté en historique git de cette story (commit avant le pivot
2026-04-26).

Le repli n'est pas une honte — c'est ce qui rend le gating crédible.

---

## 📦 Done when

- [ ] Phase 1 commitée
      (`story(S10): refine — voice mode v2 (Eleven Agents …)`).
- [ ] Phase 2 commitée
      (`feat(S10): voice mode conversationnel — …`),
      `make lint` + `make test` verts, `make test-integration`
      voice e2e vert (au moins U1 fluide en live).
- [ ] Phase 3 approuvée (`review(S10): approved`).
- [ ] Cahier `docs/cahier-des-charges.md` §19 amendé en cohérence.
- [ ] `docs/deployment.md` §3 ter rédigé (création Eleven Agent +
      env vars).
- [ ] `EVALUATION.md` ajout d'une entrée 5ème scénario voice
      ("clique sur le micro et demande LVMH").
- [ ] Loom de démo mis à jour avec scénario voice (~20 s) si gating
      ok.
- [ ] Ligne S10 mise à jour `✅` dans `docs/stories/README.md`.
- [ ] Push effectué sur `claude/builder-evaluation-exercise-34Iyu`.

---

## 📚 Sources de référence (à valider phase 1)

- ElevenLabs Eleven Agents — https://elevenlabs.io/agents
- Custom LLM integration — https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm
- Conversation flow / soft timeout — https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow
- Conversational AI 2.0 changelog — https://elevenlabs.io/docs/changelog/2026/3/9
- Build Real-Time Voice Agent 2026 (third-party guide) —
  https://deepgram.com/learn/elevenlabs-real-time-voice-agent

(Toutes ces URLs ont été vues lors de la pré-recherche utilisateur du
2026-04-26. À re-valider en phase 1 — versions de SDK et URL exactes
peuvent bouger.)
