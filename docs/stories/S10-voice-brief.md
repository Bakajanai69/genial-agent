# S10 — 🎯 Stretch : Voice Mode conversationnel via ElevenLabs Agents

> **Statut** : 🟢 phase 1 raffinée 2026-04-26 (gating en attente)
> **Durée estimée** : 4-6 h dev (POC) + 1 h gating + 1 h doc
> **Parallélisable avec** : —
>
> **Pivot 2026-04-26 (raffiné post-phase 1)** : la version initiale de
> cette story prévoyait un **simple TTS** de la réponse texte (un brief
> radio de 30-40 s lu par Gaëlle/Guillaume après la réponse). On
> repivote vers un **vrai voice mode conversationnel** style ChatGPT
> Voice : l'utilisateur parle, l'agent comprend (ASR), enchaîne ses
> tools Pappers, répond oralement (TTS) en temps réel, sans bouton
> d'enregistrement manuel. Hypothèses techniques 2026 toutes validées
> en ligne — voir §"Phase 1 — Elicitation Agent" plus bas.
>
> **Décisions phase 1 figées** (détails §"Décisions phase 1") :
> 1. **Adapter SSE OpenAI** monté via un nouveau
>    `src/genial_agent/voice/mount.py` qui clone le pattern
>    [`observability/mount.py`](../../src/genial_agent/observability/mount.py)
>    (prepend de routes sur `chainlit.server.app.router.routes`,
>    flag `_MOUNTED` idempotent). **Pas de refactor `uvicorn` / `asgi.py`** :
>    le launch `chainlit run app.py` reste intact (Dockerfile:93).
> 2. **Auth endpoint** : Bearer token `ELEVEN_AGENT_SHARED_TOKEN`
>    configuré côté ElevenLabs Workspace Secrets + middleware
>    timing-safe (`hmac.compare_digest`) côté FastAPI.
> 3. **Voice-friendly** : injection système prompt runtime (suffixe au
>    `SYSTEM_PROMPT_AGENT` quand `voice_mode=on`), **pas** de Haiku
>    reformulateur (latence cumulée prohibitive).
> 4. **Streaming narratif tool steps** : mapping ≤6 entrées dans
>    `narrate.py`, neutre, FR.
> 5. **Sécurité widget** : agent **public + domain allowlist** côté
>    ElevenLabs Security tab (Railway prod + localhost). Pas de signed
>    URL pour la démo (sécurité portée par le custom LLM endpoint).

---

## 📍 Contexte

ElevenLabs a sorti **Eleven Agents** (anciennement Conversational AI,
renommé en 2026 en parallèle de v2.0) — une stack conversationnelle
GA qui packagee :

- **ASR** (speech-to-text fine-tuné multilingue, FR validé en prod
  ElevenLabs).
- **Turn-taking model propriétaire** (VAD + analyse prosodie /
  micro-pauses) qui détecte fin de parole, hésitations, interruptions
  — **pas besoin de bouton d'enregistrement manuel**. Trois niveaux
  d'eagerness disponibles : **Eager / Normal / Patient** (cf.
  [conversation flow](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow)).
- **TTS** low-latency sur 5 k+ voix, 70+ langues, voix Gaëlle /
  Guillaume validées tier `growing_business`.
- **Custom LLM** branchable via endpoint OpenAI-compatible SSE
  (`POST /v1/chat/completions` ou `POST /v1/responses`) → on peut
  **brancher notre agent Genial existant** sans rien changer à sa
  logique cerveau (Claude Haiku/Sonnet, MCP Pappers, payload vault,
  caps). Format SSE figé : chunks `data: {json}\n\n`, terminaison
  `data: [DONE]\n\n`, body `ChatCompletionChunk` minimal
  `{choices: [{delta: {content: "..."}, index: 0}]}`. Doc :
  [custom-llm](https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm).
- **Soft timeout** natif : si le custom LLM tarde, ElevenLabs prononce
  un filler statique configurable (`message`, 1-200 chars, default
  `"Hhmmmm...yeah."`) ou LLM-généré (`use_llm_generated_message=true`,
  +1 LLM call) après un délai `timeout_seconds` (range 0.5-8.0, défaut
  3 s), 1 fois par tour, pour éviter le silence gênant pendant que
  Pappers répond. **Décision** : statique FR `"Un instant, je consulte
  les données…"` (latence prédictible, pas de surcoût LLM).
- **claude-sonnet-4-6 supporté nativement** depuis le 9 mars 2026
  ([changelog](https://elevenlabs.io/docs/changelog/2026/3/9)). Mais
  on garde le **custom LLM endpoint** pour conserver MCP Pappers +
  notre routing Haiku/Sonnet + nos guardrails.

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

- [`docs/cahier-des-charges.md`](../cahier-des-charges.md) §19 — diff
  d'amendement préparé en phase 1, à appliquer par le dev agent en
  phase 2 (cf. §"Mise à jour cahier post-phase 1").
- [`docs/pappers-mcp.md`](../pappers-mcp.md) — garde-fous MCP Pappers,
  inchangés (le voice mode ne change rien aux appels Pappers).
- ElevenLabs docs : voir §"Sources de référence" en fin de story (URLs
  validées 2026-04-26).
- Notre agent existant
  [`src/genial_agent/agent.py`](../../src/genial_agent/agent.py)
  expose déjà du streaming Anthropic — l'adapter en SSE
  OpenAI-compatible est un wrapper léger (~80-120 lignes estimées
  post-phase 1, conversion `messages` OpenAI ↔ historique Anthropic
  + drive `run_guarded_turn` + chunks SSE).

---

## 🔒 Prérequis (gating strict)

Aucun écart toléré. Cocher avant de démarrer phase 2 :

- [ ] **S09.7 mergée et stable** (commit review approved). Statut
      actuel : `🟡 dev done 2026-04-26 + 18 hotfixes/improvements live
      (review pending)`. **Bloquant tant que la review n'est pas
      `approved`**.
- [ ] URL Railway prod stable, healthcheck UptimeRobot vert.
- [ ] 3 tests officiels Pappers OK en live.
- [ ] Pack adversarial S09 ≥ 9/10 effectif.
- [ ] **Latence U1/U2 mesurée < 10 s** sur les 3 derniers runs live
      (pré-requis dur — voir §"Risques" R23 plus bas).
- [ ] Empty state + starters + SIREN cliquables opérationnels.
- [ ] `EVALUATION.md` rédigé.
- [x] **Bug crédits prewarm** (capture 2026-04-26 20:32 — appels
      `sirenisateur` parasites au boot de chaque chat) **investigué et
      fixé** :
      - commit `c4af233` : idempotence process-level via flag
        `_PREWARM_DONE` dans `mcp_pappers.py:648`.
      - commit `56666a1` : early-set du flag avant le 1er `await`
        pour résoudre la race condition observée live (4 sessions
        Chainlit parallèles entraient dans la boucle avant le post-set
        — 4 `pappers_call_ok` au lieu de 2).
      - commit `d943ae3` : rebake `data/mcp_cache.json` avec les seeds
        runtime exacts (LVMH / BNP Paribas / Carrefour / Casino
        Guichard) pour éliminer le cache miss systématique sur les
        seeds.
      - **Reste dette** (non-bloquante S10) : alignement définitif
        seeds ↔ bake (S09.8 pré-existante, hors scope ici).

## 🔑 Inputs utilisateur requis

- [x] `ELEVENLABS_API_KEY` fournie et active (probe 2026-04-24,
      tier `growing_business`, quota TTS mensuel 5 922 075 chars).
- [x] `ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD`.
- [x] `ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD`.
- [ ] **Solde crédits Pappers ≥ 100** au moment du POC voice mode
      (chaque test live consomme des crédits agent normaux). À vérifier
      sur `moncompte.pappers.fr/credits` la veille du POC.
- [ ] **Vérifier dans le dashboard ElevenLabs `Usage`** l'allocation
      Conversational AI / Eleven Agents minutes pour le tier
      `growing_business`. ⚠ Le tier couvre **explicitement les
      caractères TTS** (5,9 M chars/mois) mais **les minutes Eleven
      Agents sont facturées séparément** au tarif minute (10¢/min
      Creator/Pro, ~8¢/min Business annuel — cf.
      [pricing](https://elevenlabs.io/pricing) §Conversational AI).
      Estimation week-end : ~30 min cumul × 10¢ ≈ **3 $**. Au-dessus
      du quota inclus → bascule usage-based automatique.
- [ ] **Création d'un Eleven Agent dans le dashboard ElevenLabs** :
      voix Gaëlle (défaut), langue FR, soft timeout 3 s message
      `"Un instant, je consulte les données…"`,
      `use_llm_generated_message=false`, turn eagerness **Patient**
      (cas U3 : utilisateur peut formuler une question complexe en
      8-12 s). Custom LLM URL : `https://genial-agent-production.up.railway.app/v1/chat/completions`.
- [ ] **Génération `ELEVEN_AGENT_SHARED_TOKEN`** (32+ chars random,
      `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
      À enregistrer **simultanément** :
      - dans **Workspace Secrets ElevenLabs** (dashboard > Workspace
        > Secrets > Add Secret > nom `ELEVEN_AGENT_SHARED_TOKEN`,
        value = la chaîne) ;
      - dans **Railway Project Variables** côté nous ;
      - dans `.env` local (gitignoré).
      Puis dans la config du Custom LLM agent : ajouter un header
      `Authorization: Bearer ${ELEVEN_AGENT_SHARED_TOKEN}` qui pointe
      sur le secret workspace.
- [ ] **Récupérer `ELEVEN_AGENT_ID`** (visible dans l'URL du dashboard
      après création de l'agent, format `agent_xxxxxxxxxxxxxxxxxxxxx`).
      À ajouter en Railway env + `.env` local. Pas un secret (ID public
      consommable par n'importe quel widget — la sécurité passe par le
      domain allowlist + le Bearer token côté custom LLM).
- [ ] **Domain allowlist** côté ElevenLabs Security tab de l'agent :
      `genial-agent-production.up.railway.app`, `localhost:8000`,
      `localhost:8765`. Sans ça, le widget refuse de se connecter
      depuis un domaine non listé.
- [ ] `ENABLE_VOICE_MODE=true` dans Railway + `.env` local **après**
      merge S10 + gating respecté. Aujourd'hui valeur cible `false`.

> **Renommage `ENABLE_VOICE_BRIEF` → `ENABLE_VOICE_MODE`** : S10 v1
> avait `ENABLE_VOICE_BRIEF` (config + `.env.example` actuels). La
> phase 2 doit **renommer** le flag dans :
> `src/genial_agent/config.py:26`, `.env.example:15`, `docs/cahier-des-charges.md`
> §19.6, `docs/stories/README.md` §"Avant S10", `docs/stories/S01-scaffold.md:157`,
> `docs/stories/S02-mcp-pappers.md:681`, `docs/stories/S08-deployment.md:65/975/1604`,
> `docs/deployment.md:60`. Aucun code applicatif ne lit encore le flag
> (grep confirme — il n'y a pas de logique conditionnée dessus dans
> `src/`), donc le renommage est cosmétique côté code.

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

## 🏗 Architecture cible (figée phase 1)

```
[mic navigateur]
    ↓
[Widget JS ElevenLabs] (embed Chainlit via custom_js)
    │   <elevenlabs-convai agent-id=$ELEVEN_AGENT_ID
    │     override-language="fr"
    │     placement="bottom-right"
    │     avatar-orb-color-1="#6040C0"
    │     avatar-orb-color-2="#9080E0"></elevenlabs-convai>
    │   <script src="https://unpkg.com/@elevenlabs/convai-widget-embed@0.5.4">
    ├── ASR ElevenLabs (FR) → texte transcrit
    ├── Turn-taking propriétaire (Patient eagerness, détecte fin parole)
    └── envoie → POST https://<railway>/v1/chat/completions
        Headers: Authorization: Bearer $ELEVEN_AGENT_SHARED_TOKEN
                 Content-Type: application/json
        Body: {messages: [...], model: "...", stream: true,
               tools: [end_call, language_detection, ...]}
                          ↓
            [verify_eleven_request middleware]
            (src/genial_agent/voice/security.py)
            timing-safe hmac.compare_digest sur le Bearer
            → 401 si absent / bidon (sans logger la valeur)
                          ↓
            [Adapter OpenAI ← → Anthropic]
            (src/genial_agent/voice/openai_adapter.py)
            - convertit messages OpenAI → ConversationState
            - injecte SUFFIXE voice-friendly au system prompt
              quand voice_mode=on (cf. voice_prompt.py)
            - drive run_guarded_turn (agent existant inchangé)
            - **ignore** les `tools` end_call/etc. envoyés par Eleven
              (notre brain n'a pas besoin de ces system tools)
                          ↓
            [run_guarded_turn] ← agent existant 100% inchangé
                          ↓ stream events
            ┌─────────────┴─────────────┐
            ↓                           ↓
    tool_use events             text events finaux
            ↓                           ↓
    chunk SSE narratif via      chunk SSE delta.content
    narrate.py mapping          (verbatim depuis text events)
    ≤6 entrées:                  "À fin 2024, LVMH a réalisé
    "Je cherche le SIREN…"      environ 84 milliards d'euros…"
    "Je consulte les comptes…"
            └─────────────┬─────────────┘
                          ↓
    Format SSE OpenAI Chat Completions:
    data: {"choices": [{"delta": {"content": "..."}, "index": 0}]}\n\n
    ...
    data: [DONE]\n\n
                          ↓
            renvoyé à Eleven Agent en SSE (Content-Type: text/event-stream)
                          ↓
            [TTS ElevenLabs streaming] (voice = Gaëlle ou Guillaume)
                          ↓
[haut-parleur navigateur]

Si pause LLM > 3 s → ElevenLabs déclenche le filler statique configuré
"Un instant, je consulte les données…" → reprend dès qu'un chunk arrive.
```

**Principes** :
- Notre **agent reste 100 % inchangé** côté logique (tools, MCP, vault,
  caps, routing Haiku/Sonnet). Voice mode est une couche I/O.
- Le **cap dur 60 s wall-clock** côté agent reste actif — si U3 dépasse
  60 s, le cap-as-UX-event de S09.7 se déclenche, l'adapter émet le
  chunk text final (synthèse partielle) + `[DONE]`. L'utilisateur peut
  dire "continue" oralement qui repassera par ASR → message standard
  → relance pipeline.
- Le **streaming narratif** est généré côté wrapper (pas par le LLM
  cerveau) à partir des events `tool_use` que `run_guarded_turn` émet
  déjà — pas de second LLM nécessaire.
- **Mount via le pattern existant** : on étend
  [`observability/mount.py`](../../src/genial_agent/observability/mount.py)
  qui prepend déjà `/health` et `/stats` sur `chainlit.server.app`.
  Ajouter `/v1/chat/completions` au même endroit (ou créer
  `voice/mount.py` qui fait pareil — clean separation). **Pas de
  refactor `uvicorn`/`asgi.py`** : le launch `chainlit run app.py -h`
  du Dockerfile reste intact (cf. Dockerfile:93).

---

## 🧭 Phase 1 — Elicitation Agent (résolue 2026-04-26)

Toutes les hypothèses ont été validées en ligne contre les docs
ElevenLabs 2026 (mars-avril 2026) et le code existant. Les points
ouverts ont été tranchés.

### Recherches obligatoires — toutes résolues

- [x] **Eleven Agents — pricing 2026** : facturation **en minutes**,
      pas en caractères. Tarifs Conversational AI 2026 :
      - **10 ¢/min** sur Creator/Pro, **8 ¢/min** sur Business annuel,
        plus bas en Enterprise.
      - Ratio crédits : ~10 000 crédits = 10 min haute qualité ou
        15 min interaction agent (varie selon LLM custom).
      - Le tier `growing_business` couvre **TTS classique** (5,9 M
        chars/mois), **les minutes Eleven Agents sont facturées
        séparément** au tarif minute (usage-based ON par défaut sur
        Creator+).
      - **À vérifier dashboard `Usage`** : l'allocation incluse pour
        ce tier (peut être 0 → 100 % pay-as-you-go) — voir checklist
        inputs ci-dessus. Estimation week-end 30 min × 10 ¢ = **3 $**.
      - Source :
        [pricing](https://elevenlabs.io/pricing),
        [we-cut-our-pricing](https://elevenlabs.io/blog/we-cut-our-pricing-for-conversational-ai).
- [x] **Custom LLM endpoint — spec exacte 2026** :
      - Format `/v1/chat/completions` SSE confirmé (chunks
        `data: {json}\n\n` terminés par `data: [DONE]\n\n`,
        Content-Type `text/event-stream`). Alternative Responses API
        `/v1/responses` (events typés `response.output_text.delta`,
        `response.completed`) — **on retient Chat Completions** car
        plus simple à wrapper depuis nos events Anthropic.
      - L'agent ElevenLabs **envoie** des `tools` natifs (`end_call`,
        `language_detection`, `transfer_to_agent`, `transfer_to_number`,
        `skip_turn`, `voicemail_detection`). Un custom LLM peut les
        **ignorer** : ne pas les renvoyer en tool_calls SSE et ne pas
        les utiliser dans le routing Anthropic. ElevenLabs accepte
        sans erreur.
      - **Auth entrante = Bearer header configuré côté dashboard**.
        Pas de signature HMAC, pas d'IP allowlist (ElevenLabs ne
        publie pas de plage d'IP stable pour les requêtes outbound).
        Pattern : créer un Workspace Secret `ELEVEN_AGENT_SHARED_TOKEN`
        + ajouter au custom LLM un header
        `Authorization: Bearer ${ELEVEN_AGENT_SHARED_TOKEN}`.
        ElevenLabs supporte aussi OAuth2 / JWT depuis le 23 mars 2026
        ([changelog](https://elevenlabs.io/docs/changelog/2026/3/23)),
        mais Bearer reste le plus simple pour notre cas.
      - Soft timeout : range `0.5-8.0 s`, défaut `3.0 s`, message
        statique 1-200 chars (default `"Hhmmmm...yeah."`),
        `use_llm_generated_message` boolean (false par défaut). Doc :
        [conversation-flow](https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow).
- [x] **Widget JS embeddable — intégration Chainlit** :
      - Embed via **custom element** `<elevenlabs-convai>` + script
        CDN ou self-hosted. **Latest npm** :
        [`@elevenlabs/convai-widget-embed@0.5.4`](https://www.npmjs.com/package/@elevenlabs/convai-widget-embed).
        Pin la version dans la story (cf. §"Phase 2").
      - Pattern d'intégration Chainlit : **étendre `custom_js`** dans
        `.chainlit/config.toml`. Aujourd'hui une seule entrée
        `custom_js = "/public/owner-cookie.js"` est posée (S09.7).
        Chainlit ne supporte qu'un seul `custom_js` (string, pas
        array) — donc on **fusionne** owner-cookie.js + voice-widget
        bootstrap dans un nouveau fichier
        `public/eleven-widget-bootstrap.js` qui appelle l'ancien code
        owner-cookie + injecte le custom element + le script CDN.
        Coexistence verifiée : le widget convai utilise un Web
        Component (Shadow DOM isolé) → pas de conflit CSS / DOM avec
        Chainlit. WebSocket : Eleven utilise sa propre URL (api.
        elevenlabs.io), aucun conflit avec celui de Chainlit.
      - Customisation visuelle : attributs `placement="bottom-right"`,
        `avatar-orb-color-1="#6040C0"`, `avatar-orb-color-2="#9080E0"`,
        `start-call-text="Parler à l'agent"`,
        `listening-text="J'écoute…"`, `speaking-text="Je réponds…"`.
      - Mode auto (turn-taking) **par défaut**. Pas de mode push-to-talk
        natif côté embed widget (existe côté SDK JS bas niveau si
        besoin). Pour la démo, l'auto-mode + Patient eagerness suffit.
      - Source :
        [widget customization](https://elevenlabs.io/docs/eleven-agents/customization/widget),
        [embedding-guide deepwiki](https://deepwiki.com/elevenlabs/packages/5.3-embedding-guide).
- [x] **SDK Python ElevenLabs Agents 2026** :
      [`elevenlabs` v2.44.0](https://pypi.org/project/elevenlabs/)
      (mars 2026, `ConversationalAi.Conversation` côté client). **Non
      utilisé pour notre cas** : on expose juste un endpoint FastAPI,
      le widget JS gère ASR + TTS côté navigateur. Le SDK servirait
      uniquement si on voulait piloter une session voice depuis un
      script Python (hors scope démo).
- [x] **Compatibilité Chainlit data layer** : voice mode ne **bypass
      pas** le data layer. Comme l'endpoint `/v1/chat/completions` est
      hors du flux Chainlit standard (pas de `cl.user_session`
      disponible côté custom routes — cf.
      [Issue Chainlit #2123](https://github.com/Chainlit/chainlit/issues/2123)),
      les sessions voice **ne créent pas de thread Chainlit** dans la
      sidebar. **Décision** : c'est acceptable pour le POC démo
      (l'évaluateur teste voice → ferme l'onglet → revient sur le
      texte). Une session voice = un échange éphémère côté UI. Si on
      veut persister, c'est un next-step (S10.5 hypothétique :
      brancher l'adapter sur le data layer via un session_id custom).
- [ ] **Latence end-to-end mesurée** sur cas U1 (fiche LVMH simple) :
      à mesurer en phase 2 dev — **target < 8 s** ASR + custom LLM
      streaming + TTS first-byte. Les composantes attendues :
      - ASR ElevenLabs : ~600-800 ms (décodage + finalisation tour).
      - Custom LLM (Anthropic via custom endpoint) : 2-5 s sur U1
        (1 ou 2 tool calls Pappers + génération finale).
      - TTS streaming : ~300-500 ms time-to-first-byte audio.
      - **Cumul attendu : 3-6 s sur U1**. Si > 8 s, escalade vers
        Plan B fallback brief vocal v1 §"Plan B".

### Décisions phase 1 (figées)

1. **Prompt voice-friendly = injection system prompt** (pas de Haiku
   reformulateur).
   - Implémentation : `src/genial_agent/voice/voice_prompt.py` expose
     une constante `VOICE_SUFFIX` qui est **concaténée** au
     `SYSTEM_PROMPT_AGENT` (`src/genial_agent/prompts.py:15`) quand
     `voice_mode=on`.
   - Contenu cible :

     ```text
     ## Mode vocal actif (voice_mode=on)
     Ta réponse sera lue à voix haute par un système TTS. En conséquence :
     - Ne jamais énoncer de SIREN à l'oral (la suite de chiffres casse l'oreille). Si une référence à l'entité est nécessaire, dis "selon Pappers" ou "d'après les données officielles".
     - Arrondir tous les chiffres : "84 milliards d'euros" plutôt que "84,1 Md€", "environ 350 000 salariés" plutôt que "346 478".
     - Style narratif fluide pour l'oreille, pas de bullet points, pas de listes Markdown.
     - Utilise des transitions naturelles ("Par ailleurs", "À noter que", "Pour le contexte").
     - Limite la réponse à environ 100-120 mots (~40 s à débit normal). Si la question demande plus, propose à l'oral de basculer en texte.
     - Garde le sourçage en interne (sert le validateur §C5) mais n'énonce pas la date du bilan en mode "format ISO". Préfère "selon le bilan 2024" ou "à fin décembre dernier".
     ```

   - Justification du choix vs Haiku reformulateur :
     - +400-600 ms latence du reformulateur tue le voice mode (cumul
       ASR ~700 ms + LLM ~3 s + reformul. 500 ms + TTS first-byte
       400 ms = ~4,6 s déjà sur U1, alors que la cible est < 8 s).
     - Le système prompt est éprouvé (cf. cahier §14.3 C2 : 6 couches
       de garde-fous).
     - Si la qualité orale est insuffisante en phase 2 dev, on garde
       l'option de basculer sur Haiku reformulateur **off-path** (le
       texte UI part en premier, la voix arrive après) — c'est la
       même architecture que le brief vocal v1.

2. **Streaming narratif tool steps = mapping ≤6 entrées dans
   `narrate.py`** :

   ```python
   # src/genial_agent/voice/narrate.py
   _TOOL_NARRATION = {
       "sirenisateur":          "Je cherche le SIREN…",
       "recherche-entreprises": "Je regarde les chiffres clés…",
       "comptes-entreprise":    "Je consulte les comptes…",
       "recherche-dirigeants":  "Je vérifie les mandats…",
       "cartographie-entreprise": "Je trace la cartographie…",
       "payload_inspect":       "Je détaille les données…",
   }
   _DEFAULT = "Je consulte Pappers…"
   ```

   - Pas d'entité hardcodée (pas de "LVMH" ni "Carrefour") — cohérent
     avec philosophie S09.7 "agent adaptable".
   - Émis sur chaque `tool_use` event sous forme de chunk SSE
     `delta.content` du même flux que la réponse finale (concatenation
     transparente côté ElevenLabs TTS — il streame sur "voice-friendly
     boundaries", càd virgules + fins de phrase).
   - Test grep : `tests/unit/test_S10_narrate.py` doit échouer si une
     entité (LVMH, Carrefour, BNP, Casino) apparaît dans
     `narrate.py` ou `voice_prompt.py`.

3. **Sécurisation endpoint = Bearer token partagé** :
   - Middleware `src/genial_agent/voice/security.py` — `verify_eleven_request`
     vérifie le header `Authorization: Bearer <token>`.
   - **Comparaison timing-safe** via `hmac.compare_digest(token, settings.ELEVEN_AGENT_SHARED_TOKEN.encode())`
     pour empêcher les timing attacks.
   - Token absent ou bidon → `Response(status_code=401)`. **Ne jamais
     logger la valeur reçue** (ni dans les logs structurés, ni dans
     un message d'erreur retourné). On peut logger `auth_attempt_rejected`
     avec un hash tronqué `sha256(token)[:8]` à des fins d'audit.
   - Charger via `settings.ELEVEN_AGENT_SHARED_TOKEN` (nouveau champ
     `Settings`, cf. §"Phase 2").
   - Pas d'IP allowlist (ElevenLabs n'a pas de plage stable).
   - **Fallback feature flag** : si `ENABLE_VOICE_MODE=false`,
     l'endpoint répond 404 (route non montée) — empêche l'exploitation
     même si le secret fuite.

4. **Cas d'erreur produit** :
   - **ElevenLabs Agent KO côté widget** (CDN unpkg down, agent
     supprimé, etc.) : le widget loggue dans la console JS et reste
     muet. **Aucun fallback automatique côté backend** (le widget est
     une UI séparée, le chat texte Chainlit reste opérationnel).
     L'évaluateur voit le bouton micro qui ne réagit pas → effet
     dégradé connu. Documenter dans `EVALUATION.md` que le mode texte
     est le golden path.
   - **Custom LLM endpoint timeout** : ElevenLabs déclenche le soft
     timeout après 3 s (filler "Un instant, je consulte les
     données…"). Si le SSE ne revient toujours pas après le filler,
     ElevenLabs ferme la session avec un message d'erreur générique
     côté audio. **Côté nous** : on émet de toute façon des chunks
     narratifs (`narrate.py`) au fil des tool calls — donc le silence
     LLM > 3 s est rare en pratique. Le seul cas pathologique : un
     `run_guarded_turn` qui s'enlise sur un Pappers timeout (notre
     cap dur 60 s prend le relais).
   - **User parle pendant que l'agent répond** (interruption native
     ElevenLabs) : ElevenLabs ferme la connexion SSE côté client →
     Starlette détecte `ClientDisconnect` côté serveur. L'adapter
     attrape `asyncio.CancelledError` (ou la propage depuis le
     `ContextManager` SSE) → `gen.aclose()` sur le générateur
     `run_guarded_turn` → libération `state.lock` (pattern S03 +
     S09.7 invariant I5, déjà éprouvé en
     [`app.py:347-356`](../../src/genial_agent/app.py)).

### Mise à jour cahier post-phase 1 (à faire en phase 2 dev)

Le commit phase 2 doit amender `docs/cahier-des-charges.md` §19. Diff
préparé ci-dessous (à appliquer par le dev agent) :

- **§19** : titre `"Stretch — Mode \"brief vocal\" immersif"` →
  `"Stretch — Voice mode conversationnel via Eleven Agents"`.
- **§19.2 Concept produit** : remplacer le brief radio post-réponse
  par la conversation duplex temps réel décrite §"Architecture cible"
  ci-dessus.
- **§19.3 Architecture** : remplacer le diagramme `briefer Haiku +
  cl.Audio` par le diagramme widget JS + custom LLM endpoint Bearer.
- **§19.4 D1-D8 Décisions produit** : conserver la table mais
  remplacer "toggle ON/OFF" par "bouton micro widget", et "cap 20
  briefs/session" par "cap minutes/session côté ElevenLabs dashboard"
  (à configurer en phase 2 dev).
- **§19.5 Voix** : inchangé (Gaëlle/Guillaume IDs validés).
- **§19.6 Configuration et secrets** : remplacer
  `ENABLE_VOICE_BRIEF=true` par `ENABLE_VOICE_MODE=true`. Ajouter
  `ELEVEN_AGENT_ID=agent_xxxxxxx`, `ELEVEN_AGENT_SHARED_TOKEN=<secret>`.
- **§19.7 Prompt du briefer Haiku** : remplacer par "Suffixe
  voice-friendly injecté au system prompt principal" + le contenu
  cible figé en §"Décisions phase 1" point 1.
- **§19.8 R19-R24** : merger avec §"Risques spécifiques" S10 (R23-R30
  ci-dessous), supprimer R19/R20 propres au TTS post-réponse v1.
- **§19.9 Coût** : recalculer 30 min × 10 ¢ = **3 $** (vs $3 original).
- **§19.10 Gain démo** : remplacer "scénario 7 du Loom — brief radio"
  par "scénario 7 du Loom — vraie conversation vocale duplex".
- **§19.11 Livrables** : L12 = widget vocal opérationnel
  (ex-`toggle vocal`). L13 = sélecteur Gaëlle/Guillaume (depuis
  dashboard ElevenLabs). L14 = scénario voice ajouté au Loom. L15 =
  entrée `EVALUATION.md`.
- **§19.12 Robustesse intégration ElevenLabs** : remplacer
  §19.12.1-§19.12.7 par les nouvelles sections (auth Bearer, SSE
  format, soft timeout 3 s, cancellation via ClientDisconnect).
  L'idempotence applicative §19.12.1 (cache TTS sha256) **devient
  caduque** : le widget ne fait pas de "réécouter" automatique, et
  les sessions voice sont éphémères → on retire la complexité.

### Commit phase 1

`story(S10): refine — voice mode v2 (Eleven Agents + custom LLM endpoint)`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier (figés post-phase 1)

| Action | Fichier | Rôle |
|---|---|---|
| ➕ | `src/genial_agent/voice/__init__.py` | namespace |
| ➕ | `src/genial_agent/voice/openai_adapter.py` | endpoint `/v1/chat/completions` SSE qui wrap `run_guarded_turn`. Convertit messages OpenAI ↔ Anthropic. Émet narration sur tool_use. Cancellable via `ClientDisconnect`. |
| ➕ | `src/genial_agent/voice/narrate.py` | mapping `tool_name` → phrase narrative ("Je consulte X…"), neutre, ≤ 6 entrées (cf. phase 1). |
| ➕ | `src/genial_agent/voice/voice_prompt.py` | constante `VOICE_SUFFIX` + helper `compose_voice_system_prompt()` qui retourne `SYSTEM_PROMPT_AGENT + "\n\n" + VOICE_SUFFIX` (pas de Haiku reformulateur). |
| ➕ | `src/genial_agent/voice/security.py` | guard `verify_eleven_request` — Bearer token timing-safe via `hmac.compare_digest`. **Pas d'IP allowlist** (ElevenLabs ne publie pas de plage stable). |
| ➕ | `src/genial_agent/voice/mount.py` | `mount_voice_routes()` qui prepend `/v1/chat/completions` sur `chainlit.server.app.router.routes` quand `settings.ENABLE_VOICE_MODE` est vrai. **No-op si flag false** (route absente, surface d'attaque nulle). Calqué sur le pattern de [`observability/mount.py`](../../src/genial_agent/observability/mount.py). |
| ✏️ | `src/genial_agent/app.py` | (a) appeler `mount_voice_routes()` après `mount_routes()` (l. 78). Pas de modification du `@cl.on_chat_start` (voice mode passe par un endpoint séparé, pas par `cl.user_session`). |
| ✏️ | `src/genial_agent/config.py` | renommer `ENABLE_VOICE_BRIEF` → `ENABLE_VOICE_MODE`. Ajouter `ELEVEN_AGENT_ID`, `ELEVEN_AGENT_SHARED_TOKEN`. |
| ✏️ | `.env.example` | documenter les 3 nouvelles vars (renommage flag + 2 nouvelles). |
| ✏️ | `.chainlit/config.toml` | remplacer `custom_js = "/public/owner-cookie.js"` par `custom_js = "/public/eleven-widget-bootstrap.js"` (ce nouveau fichier appelle l'ancien `owner-cookie.js` import + injecte le widget convai si `window.GENIAL_VOICE_MODE_ENABLED`). |
| ➕ | `public/eleven-widget-bootstrap.js` | bootstrap : (1) load `owner-cookie.js` inline ou via `import` ES module ; (2) injecte `<elevenlabs-convai agent-id=...>` + `<script src="https://unpkg.com/@elevenlabs/convai-widget-embed@0.5.4">` si `window.GENIAL_VOICE_MODE_ENABLED`. Le flag global est posé via une `<meta name="genial-voice-mode" content="true">` rendue côté Chainlit selon `settings.ENABLE_VOICE_MODE` (à brancher dans `app.py` via `cl.context` si possible, sinon via `custom_meta_url`). |
| ✏️ | `src/genial_agent/observability/stats.py` | compteurs `voice_sessions_total`, `voice_custom_llm_calls`, `voice_chars_tts`, `voice_narration_chunks_emitted`, `voice_cancelled_total` (interruption user). Pas de `voice_minutes_cumulated` côté nous : ElevenLabs facture en minutes côté leur dashboard, on ne re-mesure pas. |
| ✏️ | `docs/cahier-des-charges.md` | §19 amendé (diff préparé en phase 1, à appliquer par le dev agent — cf. §"Mise à jour cahier post-phase 1"). |
| ✏️ | `docs/deployment.md` | section §3 ter — création de l'Eleven Agent dashboard, binding endpoint custom LLM, env vars `ENABLE_VOICE_MODE`, `ELEVEN_AGENT_ID`, `ELEVEN_AGENT_SHARED_TOKEN`, configuration Workspace Secret côté ElevenLabs, domain allowlist Security tab. |
| ➕ | `tests/unit/test_S10_openai_adapter.py` | tests structurels du wrapper SSE : format chunks `data: {json}\n\n`, terminaison `[DONE]`, conversion `messages` OpenAI → historique Anthropic, émission narration sur tool_use, cancellation propre sur `ClientDisconnect`. |
| ➕ | `tests/unit/test_S10_narrate.py` | tests mapping tool_name → phrases ; **fail si entité hardcodée** (`assert "LVMH" not in module.__source__`, idem Carrefour/BNP/Casino). |
| ➕ | `tests/unit/test_S10_voice_prompt.py` | tests : `compose_voice_system_prompt()` contient le suffixe ; `SYSTEM_PROMPT_AGENT` non muté. |
| ➕ | `tests/unit/test_S10_security.py` | tests guard endpoint (token absent → 401, token bidon → 401, token correct → 200, comparison timing-safe asserted via `hmac.compare_digest` mock). |
| ➕ | `tests/unit/test_S10_mount.py` | tests : `mount_voice_routes()` ajoute la route si `ENABLE_VOICE_MODE=true`, no-op sinon ; idempotent. |
| ➕ | `tests/integration/test_S10_voice_e2e.py` | live-only `@pytest.mark.integration` : crée un client `httpx` qui POST sur `/v1/chat/completions` avec Bearer correct, parse les chunks SSE, assert présence narration `"Je cherche le SIREN…"` puis texte final non vide. **Ne teste pas l'audio TTS** (c'est ElevenLabs côté navigateur — testé manuellement à l'étape 6). |

### Étapes (séquentielles, ordre figé phase 1)

**Étape 0 — Setup ElevenLabs (~30 min, blocking inputs utilisateur)**
- Créer un Eleven Agent dans le [dashboard ElevenLabs](https://elevenlabs.io/agents) :
  - Voice : Gaëlle (`tKaoyJLW05zqV0tIH9FD`).
  - Language : `fr` (override-language au niveau widget aussi).
  - LLM : **Custom LLM** → URL
    `https://genial-agent-production.up.railway.app/v1/chat/completions`,
    model name libre (ex `genial-agent-claude`), header `Authorization`
    avec value `Bearer ${ELEVEN_AGENT_SHARED_TOKEN}` (le secret pointe
    sur le Workspace Secret du même nom).
  - Conversation flow : turn eagerness `Patient`, soft timeout
    `timeout_seconds=3.0`, `message="Un instant, je consulte les
    données…"`, `use_llm_generated_message=false`.
  - Security tab → Allowlist : `genial-agent-production.up.railway.app`,
    `localhost:8000`, `localhost:8765`. Authentication = `disabled`
    (agent public — la sécurité est portée par le Bearer côté custom
    LLM, pas par le widget).
- Récupérer `ELEVEN_AGENT_ID` (URL dashboard, format
  `agent_xxxxxxxxxxxxxxxxxxxxx`).
- Générer `ELEVEN_AGENT_SHARED_TOKEN` (32+ chars,
  `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
  L'enregistrer simultanément dans (a) Workspace Secret ElevenLabs,
  (b) Railway Project Variables, (c) `.env` local.
- Renommer `ENABLE_VOICE_BRIEF=false` → `ENABLE_VOICE_MODE=false` dans
  Railway + `.env` local (le passage à `true` se fait au merge S10).

**Étape 1 — Endpoint adapter** (~2 h)
- Tests unit `test_S10_openai_adapter.py` (TDD).
- Implémenter `openai_adapter.py` qui :
  - reçoit `{messages, model, stream: true, tools?}` au format OpenAI
    Chat Completions ;
  - **ignore** les `tools` Eleven natifs (`end_call`, etc.) — ne les
    propage ni vers Anthropic ni en tool_calls SSE retour ;
  - convertit `messages` en historique Anthropic compatible
    `ConversationState` (rôle `assistant` / `user` / `system` →
    structure `state.history`) ;
  - **session_id** : utilise un UUID stable par appel ElevenLabs si
    `body["user"]` est fourni (ID de session ElevenLabs), sinon
    `uuid.uuid4().hex` éphémère ;
  - injecte `compose_voice_system_prompt()` (suffixe voice-friendly)
    via paramètre `system_prompt_override` à passer à `run_guarded_turn`
    — **action requise** : ajouter ce paramètre optionnel à la
    signature `run_guarded_turn` pour ne pas dupliquer le pipeline
    (modif unique non-breaking : default `None`, comportement actuel
    inchangé) ;
  - drive `run_guarded_turn` ;
  - convertit chaque event Anthropic en chunk SSE OpenAI :
    - `text` event → `data: {"id": "<uuid>", "object": "chat.completion.chunk", "choices": [{"delta": {"content": "..."}, "index": 0}]}\n\n` ;
    - `tool_use` event → chunk SSE de narration via `narrate.py`
      (même format `delta.content`) ;
    - `routing_done` final → chunk avec `finish_reason: "stop"` +
      `data: [DONE]\n\n` ;
  - capture `asyncio.CancelledError` ou `ClientDisconnect` Starlette
    → `gen.aclose()` sur le generator + `state.lock` libéré
    (pattern S03+S09.7) + incrémente `voice_cancelled_total`.
- Mount via le pattern existant de `observability/mount.py` :
  ```python
  # voice/mount.py
  from chainlit.server import app as cl_app
  from starlette.routing import Route
  from genial_agent.config import settings
  from genial_agent.voice.openai_adapter import chat_completions
  from genial_agent.voice.security import verify_eleven_request

  _MOUNTED = False

  def mount_voice_routes() -> None:
      global _MOUNTED
      if _MOUNTED or not settings.ENABLE_VOICE_MODE:
          return
      cl_app.router.routes.insert(
          0,
          Route(
              "/v1/chat/completions",
              endpoint=_guarded_endpoint,  # wrap chat_completions avec verify_eleven_request
              methods=["POST"],
          ),
      )
      _MOUNTED = True
  ```

**Étape 2 — Sécurisation endpoint** (~45 min)
- `security.py` : middleware qui vérifie `Authorization: Bearer
  <ELEVEN_AGENT_SHARED_TOKEN>`. Comparaison via `hmac.compare_digest`
  (timing-safe). Rejeter 401 sans log de la valeur (logger un
  `auth_attempt_rejected` avec un hash tronqué `sha256(token)[:8]`
  pour traçabilité audit).
- Tests unit `test_S10_security.py` (3 cas : absent, bidon, valide ;
  vérifier que `hmac.compare_digest` est bien appelé via spy).
- Vérifier que sans `ENABLE_VOICE_MODE=true`, la route n'est même pas
  enregistrée (test `test_S10_mount.py`). Couche de défense
  supplémentaire si le secret fuite.

**Étape 3 — Widget UI** (~1 h 15)
- Config Eleven Agent dashboard validée à l'étape 0.
- Créer `public/eleven-widget-bootstrap.js` :

  ```js
  // public/eleven-widget-bootstrap.js
  // 1. Préserver le comportement owner-cookie.js existant.
  (function preserveOwnerCookie() {
      // ...code copié verbatim de public/owner-cookie.js
      // (ou import ES module si ce dernier est rendu module-friendly)
  })();

  // 2. Injecter le widget Eleven seulement si voice mode activé.
  const meta = document.querySelector('meta[name="genial-voice-mode"]');
  if (meta && meta.content === "true") {
      const agentId = meta.dataset.agentId;
      if (!agentId) {
          console.warn("[genial] voice-mode meta missing data-agent-id");
          return;
      }
      const widget = document.createElement("elevenlabs-convai");
      widget.setAttribute("agent-id", agentId);
      widget.setAttribute("override-language", "fr");
      widget.setAttribute("placement", "bottom-right");
      widget.setAttribute("avatar-orb-color-1", "#6040C0");
      widget.setAttribute("avatar-orb-color-2", "#9080E0");
      widget.setAttribute("start-call-text", "Parler à l'agent");
      widget.setAttribute("listening-text", "J'écoute…");
      widget.setAttribute("speaking-text", "Je réponds…");
      document.body.appendChild(widget);

      const script = document.createElement("script");
      script.src = "https://unpkg.com/@elevenlabs/convai-widget-embed@0.5.4";
      script.async = true;
      document.body.appendChild(script);
  }
  ```

- Mettre à jour `.chainlit/config.toml` :
  `custom_js = "/public/eleven-widget-bootstrap.js"`.
- Pour exposer le flag + l'agent-id côté navigateur sans re-render
  Chainlit : utiliser une `<meta name="genial-voice-mode" content="…" data-agent-id="…">`
  injectée via le pattern `custom_meta_url` ou via une route
  custom `/voice-meta.html` servie par notre app FastAPI (à brancher
  dans `voice/mount.py`). **Alternative simple** : générer le bootstrap
  JS dynamiquement à partir d'un template, avec `agentId` substitué
  côté serveur au démarrage. Le dev agent choisit selon le moins
  intrusif (le 2ᵉ pattern est plus simple, pas de meta tag).
- Test manuel local : `make run` → vérifier que le bouton micro
  apparaît en bas à droite, ouvre la session, l'audio fonctionne,
  pas de conflit CSS avec la sidebar Chainlit.

**Étape 4 — Streaming narratif tool steps** (~30 min)
- `narrate.py` mapping ≤ 6 entrées (cf. snippet figé en Décisions
  phase 1 point 2).
- Wiring dans `openai_adapter.py` : sur chaque `tool_use` event,
  émettre un chunk SSE `delta.content` = `narrate(tool_name) + " "`
  (espace final pour boundary TTS-friendly côté ElevenLabs).
- Tests unit `test_S10_narrate.py` :
  - mapping retourne la phrase attendue pour chaque tool listé ;
  - default `_DEFAULT` retourné pour un tool inconnu ;
  - **fail si entité hardcodée** (grep "LVMH|Carrefour|BNP|Casino" sur
    le source du module).

**Étape 5 — Observabilité** (~30 min)
- Compteurs `stats.py` (`voice_sessions_total`,
  `voice_custom_llm_calls`, `voice_chars_tts`,
  `voice_narration_chunks_emitted`, `voice_cancelled_total`).
- Incrémenter au call-site dans `openai_adapter.py` (cohérent avec la
  règle README #3 "compteurs au call-site").
- Forwarding via `/stats` endpoint (cohérent S07).
- Test unit : `test_S10_openai_adapter.py` valide que les compteurs
  sont incrémentés.

**Étape 6 — 🔴 Boucle validation observée** (~1 h)
- Lancer le Railway prod (ou local `make run` avec une URL ngrok pour
  que ElevenLabs puisse atteindre l'endpoint).
- Tester live :
  - U1 fiche LVMH (cible : voice mode fluide < 8 s end-to-end).
  - U2 mandats Arnault (cible : fluide).
  - U3 Carrefour vs Casino (cible : viable avec narration tool steps,
    soft timeout sur les pauses ; échec acceptable si > 60 s — cap
    dur agent toujours actif).
  - Cas erreur : retirer le secret côté Workspace ElevenLabs (sans
    couper notre Bearer Railway) → vérifier que ElevenLabs renvoie
    une erreur côté navigateur sans crasher l'app texte. Puis remettre
    le secret pour la suite des tests.
  - Test interruption : parler par-dessus la réponse agent, vérifier
    via les logs structurés `voice_cancelled_total` incrémenté et
    `state.lock` libéré (pas de "session locked" sur le tour suivant).
- Mesurer : latence ASR, latence custom LLM (Anthropic), latence TTS
  first-byte, durée totale ; reporter dans
  `traces/S10_voice_metrics.md` (créer ce fichier).

**Étape 7 — Doc + commit final** (~30 min)
- Update cahier §19 (cf. diff figé en phase 1).
- Update `docs/deployment.md` §3 ter (Eleven Agent setup, Workspace
  Secret, domain allowlist).
- Update `README.md` (section voice mode + lien doc).
- Update `docs/stories/README.md` ligne S10 → 🟡 dev done.
- Update `EVALUATION.md` : ajouter scénario 5 voice (`"Clique sur le
  micro en bas à droite et dis : 'Donne-moi la fiche de LVMH'"`).
- Update `.env.example` avec les 3 nouvelles vars.
- Commit final phase 2.

### Commit final phase 2

`feat(S10): voice mode conversationnel — Eleven Agents + custom LLM endpoint + narration tool steps`

---

## 🔍 Phase 3 — Review Agent

Check-list spécifique :

- [ ] Endpoint `/v1/chat/completions` répond uniquement avec
      `Authorization` valide (test 401 sur token absent / bidon).
      Vérifier comparaison `hmac.compare_digest` (timing-safe), pas
      `==`.
- [ ] Pas de fuite de secret (token Eleven Agent, MCP key,
      `ANTHROPIC_API_KEY`) dans les logs structurés (grep
      `auth_attempt_rejected` doit ne montrer que le hash tronqué)
      ni dans le bundle JS côté client (`eleven-widget-bootstrap.js`
      ne doit contenir que `agent_id`, jamais `shared_token`).
- [ ] L'agent reste **strictement inchangé** côté logique : `agent.py`,
      `mcp_pappers.py`, `payload_vault.py`, `routing.py`,
      `guardrails/*.py` ne sont pas touchés (grep diff). **Exception
      tolérée** : ajout d'un paramètre optionnel
      `system_prompt_override: str | None = None` à `run_guarded_turn`
      pour injecter `compose_voice_system_prompt()` sans dupliquer le
      pipeline. Doit être non-breaking (default `None` = comportement
      actuel).
- [ ] Aucun **hardcoding de logique métier** (LVMH, Carrefour, BNP,
      Casino) dans `narrate.py`, `voice_prompt.py`, le system prompt
      voice-friendly, ni la config Eleven Agent.
- [ ] Cap dur `60 s wall-clock` reste actif, le voice mode n'introduit
      pas de bypass.
- [ ] Pack adversarial S09 **maintenu** (voice mode hors path
      n'affecte pas T1-T10).
- [ ] Pack golden G1-G5 **maintenu** en mode texte.
- [ ] Latence U1 voice mode < 8 s médian sur 5 runs live (étape 6
      phase 2). Documenter dans `traces/S10_voice_metrics.md`.
- [ ] Si voice mode KO (CDN unpkg down, API key Eleven révoquée) →
      le chat texte Chainlit reste 100 % fonctionnel sans crash.
- [ ] Feature flag `ENABLE_VOICE_MODE=false` désactive complètement
      le widget (`eleven-widget-bootstrap.js` skippe l'injection) ET
      l'endpoint (`mount_voice_routes()` no-op). Vérifier via curl :
      `POST /v1/chat/completions` doit renvoyer 404.
- [ ] Coût Eleven Agents mesuré en phase 2 dev (étape 6) documenté
      dans `traces/S10_voice_metrics.md`.
- [ ] Cancellation user (interruption) testée live : `voice_cancelled_total`
      incrémenté, `state.lock` libéré, pas d'orphan tools en cours.

Sortie attendue : `review(S10): approved` ou `docs/stories/reviews/S10-rework.md`.

---

## ⚠️ Risques spécifiques

| # | Risque | Mitigation |
|---|---|---|
| R23 | Latence U3 ≥ 60 s tue le voice mode | Streaming narratif tool steps + soft timeout natif ElevenLabs (3 s) ; documenter en `EVALUATION.md` que voice mode est optimal sur U1/U2 ; cap dur 60 s déclenche cap-as-UX (S09.7) avec partial response oralisée. |
| R24 | Endpoint custom LLM exposé sans auth → spam crédits | **Bearer token partagé** vérifié timing-safe (`hmac.compare_digest`) + endpoint **non monté** si `ENABLE_VOICE_MODE=false` (défense en profondeur). Pas d'IP allowlist (ElevenLabs n'a pas de plage stable). |
| R25 | Coût Eleven Agents inattendu (pricing minutes en 2026) | Pricing confirmé : 10 ¢/min Creator/Pro, 8 ¢/min Business annuel. Cap minutes/jour configurable côté Eleven dashboard (Quota tab) ; feature flag global `ENABLE_VOICE_MODE` désactivable à chaud via env var Railway sans redeploy. |
| R26 | Interruption utilisateur ne cancel pas `run_guarded_turn` → orphan tools en cours | Adapter capture `ClientDisconnect` Starlette / `asyncio.CancelledError` → `gen.aclose()` + libération `state.lock`. Pattern éprouvé en S09.7 ([`app.py:347-356`](../../src/genial_agent/app.py)). Compteur `voice_cancelled_total` pour observabilité. |
| R27 | Widget Eleven incompatible avec CSS Genial / Chainlit | Le widget convai utilise un Web Component (Shadow DOM isolé) — pas de bleed CSS attendu. Tester en dev local (étape 3 phase 2) avant push prod. Fallback ultime : iframe isolée (non requis sur la base de la doc 2026). |
| R28 | Auto-play audio bloqué Chrome au 1er chargement | L'utilisateur doit cliquer le bouton micro pour démarrer (interaction utilisateur explicite → autoplay autorisé pour les chunks suivants de la session). |
| R29 | ASR français de qualité variable selon accent / bruit | ASR ElevenLabs FR validé en prod (cf. doc & changelog 2026). Tester avec 2-3 accents FR différents en étape 6 phase 2. Si KO bloquant : Plan B (brief vocal v1). |
| R30 | ~~Bug crédits prewarm~~ **RÉSOLU S09.7** | Commits `c4af233` + `56666a1` + `d943ae3` : idempotence process-level + early-set du flag + bake aligné. Le voice mode peut démarrer sans amplification du bug. |
| R31 | Tier ElevenLabs `growing_business` ne couvre pas Eleven Agents minutes (TTS ≠ Conversational AI) | À vérifier côté dashboard `Usage` (input utilisateur). Si 0 min incluses → bascule usage-based (3 $ pour 30 min cumul). Acceptable. Si add-on requis non disponible → Plan B. |
| R32 | Domain allowlist ElevenLabs trop strict bloque le widget en local | Inclure `localhost:8000` + `localhost:8765` + le domaine Railway dès la création de l'agent (étape 0). Vérifier en console JS qu'il n'y a pas d'erreur `domain_not_allowed`. |
| R33 | `chainlit.server.app` non disponible au moment du `mount_voice_routes()` (import order) | Suivre le pattern `mount_routes()` existant (import tardif `from chainlit.server import app as cl_app` à l'intérieur de la fonction, pas au module-load). Idempotent via flag `_MOUNTED`. |

---

## 💰 Coût estimé pour le week-end

Pricing confirmé phase 1 (sources : [pricing](https://elevenlabs.io/pricing),
[blog cut pricing](https://elevenlabs.io/blog/we-cut-our-pricing-for-conversational-ai)) :

- **Eleven Agents** : 10 ¢/min Creator/Pro, 8 ¢/min Business annuel.
  Tier `growing_business` couvre TTS (5,9 M chars/mois) — minutes
  Eleven Agents en usage-based.
- **Week-end avec 30 min cumulées** (Fabien + équipe + nous) :
  **~3 $**. Si pricing Pro : 3 $ ; si pricing Business annuel : 2,40 $.
- **Anthropic custom LLM** (Haiku/Sonnet via notre agent) : déjà
  budgeté côté agent normal, voice mode ne double pas la consommation
  (un tour voice = un tour texte côté brain).
- **Pappers** : aucun surcoût direct (mêmes appels qu'en mode texte).
  Bug prewarm résolu en S09.7 → pas de fuite parasite.

Total stretch : **< 5 $** sur le week-end. Largement acceptable.

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

## 🔥 Phase 2.5 — Hotfixes POC live (2026-04-27)

POC live end-to-end via API ElevenLabs `simulate-conversation` puis
test audio user (Mac Safari) a révélé **9 problèmes UX/qualité**
non anticipés en story phase 1. Tous fixés le même jour, sans
nouveau scope.

### Découvertes & fixes appliqués

| # | Découverte POC live | Fix | Commit / API |
|---|---|---|---|
| F1 | `simulate-conversation` API existe (Eleven) — permet de tester end-to-end en SSE sans audio mic, gratuit | Utilisé pour tous les smoke tests | (utilisé en CI manuelle, doc `deployment.md` §3 ter) |
| F2 | URL custom LLM doit être `/v1` PAS `/v1/chat/completions` — Eleven append automatiquement `/chat/completions` | Doc `deployment.md` §3 ter mise à jour avec gotcha + setup curl | `44a5333` (doc) |
| F3 | TTS `model_id` requis pour agent FR (sinon `400 Non-english Agents must use turbo or flash v2_5`) | `eleven_flash_v2_5` puis bump `eleven_turbo_v2_5` (qualité+) | PATCH API |
| F4 | Widget `convai-widget-embed` 0.5.4 (pinné en story phase 1) **ne capture pas l'audio mic** sur navigateurs 2026 — ASR Eleven reçoit du silence (`'...'`). Format audio / WebSocket protocol changé entre 0.5.x et 0.11.x | Bump `0.5.4 → 0.11.6` | `42db515` |
| F5 | Widget styling défaut Eleven = orb bleu + textes EN ("Need help?", "Start a call", "New Call") — incohérent avec branding Genial dark mode | PATCH agent `platform_settings.widget` : couleurs Genial + textes FR + dark mode + `transcript_enabled=false` + `text_input_enabled=false` (voice-only minimal) | PATCH API |
| F6 | Voix Gaëlle = `use_case=narrative_story` (audiobooks) → robotique en mode agent. Recherche voice library FR Conversational AI | Swap → **Marine - Premium Conversational AI** parisien (`6FXyooAOTqUK8m2HWm32`) | PATCH API |
| F7 | `VOICE_SUFFIX` injecté au system prompt **insuffisant** — Haiku 4.5 retourne toujours bullets / Markdown / SIREN énoncés / dates ISO / adresses (mesuré : 10 bullets, 14 bold, 3 ISO, adresse `22 avenue Montaigne` lue à voix haute). Cause racine : section "Format de sortie" du `SYSTEM_PROMPT_AGENT` principal contredit le suffix ; Haiku suit les premières instructions | (a) v2 du suffix avec OVERRIDE explicite ⚠️ + interdictions numérotées + exemple voice OK / INTERDIT (b) **Pipeline 2-passes** : main LLM bufferé puis Haiku reformulateur dédié sur le buffer | `cef82f4` (suffix v2) + `114fa55` (reformulateur + adapter refactor) |
| F8 | Pause finale 1.2 s entre dernier chunk reformulé et `[DONE]` — `aclose()` du `turn_gen` drain le critic_async (10 s timeout) avant flush. Provoque un silence audible puis "click" de fin | Inverser ordre : yield `[DONE]` AVANT `aclose()` (best-effort cleanup en finally) | `114fa55` (hotfix C) |
| F9 | Markdown leak vers TTS (`**bold**` lu comme "astérisque astérisque", emojis ⚠️ prononcés) — `strip_audio_tags=true` côté Eleven ne couvre que les balises audio, pas le Markdown brut | `strip_markdown_for_tts()` chunk-by-chunk côté adapter (regex `**`, `_..._`, `## headers`, `- bullets`, emojis, asterisks orphelins) | `114fa55` (hotfix D) |
| F10 | Latence 16.5 s sur LVMH (Pass 1 + tool calls + Pass 2 séquentiels). Voix hashée/coupée : chunks SSE irréguliers (mini 6 chars puis gros 133 chars en burst → TTS Eleven re-buffer → pop) | (a) `optimize_streaming_latency: 3 → 1 → 0` + `stability: 0.5 → 0.75` + `model_id: flash → turbo v2_5` + `soft_timeout_config 3 s` filler statique FR (b) **A4 sentence_buffer** : groupe les deltas reformulator par phrase voice-friendly (`. ! ?`) (c) **B1 voix de meubles** rotatives ("Un instant…", "Je vérifie ça…") émises toutes les 4 s pendant Pass 1 si silence (d) **B2 skip reformulateur** si réponse main < 180 chars + sans Markdown (économise 1.5-2 s sur conversationnel) | `b432e66` (A4+B1+B2) + 4 PATCH API |

### Mesures latence avant/après (live prod via SSE direct)

| Scénario | Avant POC | Après hotfixes |
|---|---:|---:|
| "Salut, comment vas-tu ?" (B2 skip) | 3.6 s | **4.3 s** (régression marginale liée au VOICE_SUFFIX v2 plus gros, mais 1 seul appel LLM) |
| "Tu peux me parler de LVMH ?" (Pass 1 + Pass 2) | 16.5 s | **12.0 s** (-27 %) |
| Inter-chunk médian (LVMH) | 344 ms | 327 ms (sur **phrases complètes** vs deltas fragmentés avant) |

### Configuration finale agent ElevenLabs (prod)

```jsonc
{
  "tts": {
    "voice_id": "6FXyooAOTqUK8m2HWm32",        // Marine - Premium Conversational AI
    "model_id": "eleven_turbo_v2_5",
    "optimize_streaming_latency": 0,            // anti-jitter max (TTS attend plus de texte)
    "stability": 0.75,
    "similarity_boost": 0.8,
    "speed": 1.0
  },
  "turn": {
    "turn_eagerness": "normal",
    "turn_model": "turn_v2",
    "turn_timeout": 8,
    "soft_timeout_config": {
      "timeout_seconds": 3.0,
      "message": "Un instant, je consulte les données…",
      "use_llm_generated_message": false
    }
  },
  "asr": {
    "quality": "high",
    "provider": "elevenlabs",
    "user_input_audio_format": "pcm_16000",
    "keywords": ["entreprise", "société", "SIREN", "Pappers", "dirigeant", "bilan", "chiffre d'affaires", "France"]
  },
  "platform_settings.widget": {
    "avatar": {"type": "orb", "color_1": "#6040C0", "color_2": "#9080E0"},
    "bg_color": "#0a0a0a", "text_color": "#ffffff", "btn_color": "#6040C0",
    "transcript_enabled": false,
    "text_input_enabled": false,
    "dismissible": true,
    "action_text": "",
    "text_contents": {"start_call": "Parler à l'agent", "end_call": "Terminer", "listening_status": "J'écoute…", "speaking_status": "Je réponds…", "main_label": "Démo voix", ...}
  }
}
```

### Modules code ajoutés

| Fichier | Rôle |
|---|---|
| `src/genial_agent/voice/reformulator.py` | Pipeline Pass 2 : Haiku reformulateur stream + `strip_markdown_for_tts()` |
| `src/genial_agent/voice/flow.py` | Helpers UX : `sentence_buffer` (A4), `next_filler` + `FILLER_PHRASES` (B1), `should_skip_reformulator` (B2) |
| `tests/unit/test_S10_reformulator.py` | 14 tests strip markdown + system prompt + skip empty |
| `tests/unit/test_S10_flow.py` | 28 tests sentence_buffer + fillers + skip heuristic |
| `src/genial_agent/voice/openai_adapter.py` | Refactored : pipeline 2-passes + sentence_buffer + filler timer + skip B2 + `[DONE]` avant `aclose()` |
| `src/genial_agent/voice/voice_prompt.py` | VOICE_SUFFIX v2 avec OVERRIDE explicite + exemple voice OK/INTERDIT |

### Jitter résiduel (acceptable pour démo)

- **Cause** : variance temporelle des phrases du reformulateur Haiku (130 ms à 7300 ms entre 2 phrases sur LVMH live). Eleven attend la suivante → micro-pause audible.
- **Mitigation appliquée** : `optimize_streaming_latency: 0` (TTS attend plus de texte avant de parler) + `stability: 0.75`.
- **Tradeoff** : TTFT audio +500 ms vs voix nettement plus stable.
- **Si encore trop hashé** (next-step si rework) : bump `model_id: turbo_v2_5 → multilingual_v2` (+200 ms par phrase, voix ultra-stable).

### Commits live de la phase 2.5

| Commit | Sujet |
|---|---|
| `42db515` | bump convai-widget-embed 0.5.4 → 0.11.6 (ASR muet) |
| `cef82f4` | VOICE_SUFFIX v2 OVERRIDE explicite |
| `114fa55` | pipeline 2-passes + done-before-aclose + strip Markdown |
| `b432e66` | A4 sentence_buffer + B1 voix meubles + B2 skip reformulateur |
| `44a5333` | doc deployment.md §3 ter — gotchas POC live |

PATCHes API ElevenLabs (non versionnés Git) cumulés :
- Voix Gaëlle → Marine
- Couleurs orb violet + textes FR + `action_text=""`
- Dark mode (`bg_color="#0a0a0a"` + styles)
- Voice-only widget (`transcript_enabled=false` + `text_input_enabled=false`)
- TTS `flash_v2_5 → turbo_v2_5`
- `optimize_streaming_latency: 3 → 1 → 0`
- `stability: 0.5 → 0.75`
- `soft_timeout_config: timeout_seconds=3.0` + filler FR
- ASR `keywords` métier FR
- Turn eagerness `patient → normal`

---

## 📦 Done when

- [x] Phase 1 commitée
      (`story(S10): refine — voice mode v2 (Eleven Agents + custom LLM endpoint)`).
- [x] Phase 2 commitée
      (`feat(S10): voice mode conversationnel — Eleven Agents + custom LLM endpoint + narration tool steps`,
      commit `112e8fe`), `make lint` + `make test` verts (671→698 unit tests).
- [x] Phase 2.5 hotfixes POC live commités (`42db515`, `cef82f4`,
      `114fa55`, `b432e66`, `44a5333`).
- [ ] Phase 3 approuvée (`review(S10): approved`).
- [x] Cahier `docs/cahier-des-charges.md` §19 amendé (commit `112e8fe`).
- [x] `docs/deployment.md` §3 ter rédigé (commit `112e8fe`) +
      gotchas POC live (commit `44a5333`).
- [x] `EVALUATION.md` ajout du 6ème scénario voice (commit `112e8fe`).
- [ ] Loom de démo mis à jour avec scénario voice (~20 s) si gating ok.
- [x] Ligne S10 mise à jour `🟡 dev done` dans `docs/stories/README.md`.
- [x] Push effectué sur `claude/builder-evaluation-exercise-34Iyu`
      (commits jusqu'à `b432e66`).
- [ ] `traces/S10_voice_metrics.md` mises à jour avec mesures live
      définitives (Pass 1 / Pass 2 / TTFT audio / TTLB audio / coût
      Eleven minutes consommées).

---

## 📚 Sources de référence (validées phase 1, 2026-04-26)

ElevenLabs — Eleven Agents (ex-Conversational AI) :

- Vue d'ensemble — https://elevenlabs.io/docs/eleven-agents/overview
- Custom LLM integration — https://elevenlabs.io/docs/eleven-agents/customization/llm/custom-llm
- Models supportés (Anthropic, OpenAI, Google, ElevenLabs natifs) —
  https://elevenlabs.io/docs/eleven-agents/customization/llm
- Conversation flow / soft timeout — https://elevenlabs.io/docs/eleven-agents/customization/conversation-flow
- Widget customization — https://elevenlabs.io/docs/eleven-agents/customization/widget
- Get signed URL (private agents) — https://elevenlabs.io/docs/api-reference/conversations/get-signed-url
- Agent authentication — https://elevenlabs.io/docs/agents-platform/customization/authentication
- Environment variables — https://elevenlabs.io/docs/eleven-agents/integrate/environment-variables

ElevenLabs — Changelog Q1 2026 (utile pour la traçabilité) :

- 9 mars 2026 (claude-sonnet-4-6 supporté) — https://elevenlabs.io/docs/changelog/2026/3/9
- 23 mars 2026 (auth connections OAuth2/JWT/Bearer) — https://elevenlabs.io/docs/changelog/2026/3/23
- Récap avril 2026 — https://releasebot.io/updates/eleven-labs

ElevenLabs — Pricing :

- Plans publics — https://elevenlabs.io/pricing
- Blog "We cut our pricing for Conversational AI" — https://elevenlabs.io/blog/we-cut-our-pricing-for-conversational-ai

Packages NPM / SDK :

- `@elevenlabs/convai-widget-embed` v0.5.4 — https://www.npmjs.com/package/@elevenlabs/convai-widget-embed
- `elevenlabs` Python SDK v2.44.0 (mars 2026) — https://pypi.org/project/elevenlabs/
- Embedding guide deepwiki — https://deepwiki.com/elevenlabs/packages/5.3-embedding-guide

Chainlit — intégration FastAPI :

- Doc officielle — https://docs.chainlit.io/integrations/fastapi
- Issue #2123 (cl.user_session inaccessible depuis routes custom) —
  https://github.com/Chainlit/chainlit/issues/2123
- Issue #1166 (route order vs `mount_chainlit`) —
  https://github.com/Chainlit/chainlit/issues/1166

Code de référence interne (pattern à suivre) :

- [`src/genial_agent/observability/mount.py`](../../src/genial_agent/observability/mount.py)
  — pattern d'extension `chainlit.server.app.router.routes` pour
  `/health` et `/stats`. À cloner pour `voice/mount.py`.
- [`src/genial_agent/app.py:347-356`](../../src/genial_agent/app.py)
  — pattern `gen.aclose()` + libération `state.lock` à reproduire
  pour la cancellation voice.
- [`src/genial_agent/mcp_pappers.py:648-720`](../../src/genial_agent/mcp_pappers.py)
  — fix idempotence prewarm (R30 résolu).
