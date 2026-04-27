# genial-agent

Agent IA spécialisé sur les entreprises françaises, branché sur le
**MCP Pappers** (streamable-http). Construit dans le cadre d'un
exercice d'évaluation AI Builder, sur un week-end avec contraintes
familiales — découpé en 10 stories verticales (S01 → S10) tracées
dans `docs/stories/`.

🔗 **Démo live** : <https://genial-agent-production.up.railway.app>
📦 **Repo** : <https://github.com/Bakajanai69/genial-agent>

[![CI](https://github.com/Bakajanai69/genial-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bakajanai69/genial-agent/actions/workflows/ci.yml)
[![service](https://img.shields.io/website?url=https%3A%2F%2Fgenial-agent-production.up.railway.app%2Fhealth&up_message=online&down_message=offline&label=service)](https://genial-agent-production.up.railway.app/health)

> ⚠️ **Note sur le badge CI** : le quota GitHub Actions de mon compte
> personnel a été épuisé en cours de week-end (consommé par les
> nombreux push S08 → S10). Le badge apparaît rouge, mais la suite
> de tests **passe localement** : 600+ tests, `make test` ✅,
> `make lint` ✅, `make test-integration` ✅ (live, opt-in). Pour
> rejouer en local : `make install && make test && make lint`. Le
> dernier run CI vert est antérieur à l'épuisement du quota et reste
> visible dans l'historique Actions du repo.

> Le badge `service` reflète uniquement le code HTTP de `/health`
> (toujours 200 par décision S07). La fiabilité réelle (MCP Pappers
> up + agent répondant) est suivie par **UptimeRobot keyword monitor**
> sur `"status":"ok"` — cf. [`docs/deployment.md`](docs/deployment.md) §8.

---

## Ce que fait l'agent

- Réponses **sourcées** sur des entreprises FR (SIREN, dirigeants,
  bilans). SIREN cliquables vers `pappers.fr/entreprise/{siren}`.
- **Multi-turn** : « et son CA ? » après « fiche LVMH » résout le
  pronom — bannière "Entité active" en haut du chat.
- **Routing dynamique** Haiku 4.5 ↔ Sonnet 4.6 (badge `⚡` / `🧠` ;
  escalade `⚡→🧠` quand Haiku appelle `escalate_to_sonnet` ou qu'un
  cap déclenche). Cf. [`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) §5.3.
- **6 couches de garde-fous** (input gate, system prompt durci,
  safety native Claude, caps, output validator déterministe,
  Haiku-critic async). Cf. cahier §14.3.
- **Score de confiance** affiché par message via le critic
  (✓ vert / ⚠ orange / ✗ rouge).

## Quickstart local

```bash
git clone https://github.com/Bakajanai69/genial-agent.git
cd genial-agent
cp .env.example .env
# → remplir : ANTHROPIC_API_KEY + PAPPERS_API_KEY (les 2 clés sont
#   testées E2E au boot — `curl localhost:8000/health | jq .status`
#   doit renvoyer `"ok"` ; toute autre valeur (`"degraded"`/`"ko"`)
#   indique une clé invalide ou un MCP indisponible).
make install    # uv sync + pre-commit install
make run        # chainlit run sur http://localhost:8000
```

## Choix techno (1 page)

| Couche | Choix | Pourquoi |
|---|---|---|
| Agent | `anthropic` + `mcp` Python | Tool-use natif, MCP streamable-http supporté |
| Modèles | Haiku 4.5 + Sonnet 4.6 | Latence/qualité, même clé API |
| Data | MCP Pappers (streamable-http) | Imposé par le brief, unique canal officiel |
| UI | Chainlit 2.11 | Chat + streaming + step view des tool calls |
| Hosting | Railway EU-West (Amsterdam) | Déploiement < 5 min, latence Paris ~15 ms |
| Lint / deps | `ruff` + `uv` | Standard Python 2026, rapide |
| Logs | `structlog` JSON | Lisible par `railway logs \| jq` |
| Validation | Pydantic v2 | Sortie validateur déterministe |

Détails complets et alternatives (Bedrock EU, Vertex AI EU,
Microsoft Foundry EU) dans
[`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) §6.2.

## Pour aller droit aux décisions

Si tu lis ce repo en mode CTO et tu as 5 min, ouvre dans cet ordre :

1. **[`docs/architecture-decisions.md`](docs/architecture-decisions.md)**
   — 12 ADR condensés  : ce que j'ai choisi, ce que
   j'ai écarté, et pourquoi. Inclut l'architecture cible AWS si on
   productionnise.
2. **[`docs/workflow-claude-code.md`](docs/workflow-claude-code.md)**
   — comment j'utilise Claude Code en méthodo 3 phases (Elicitation /
   Dev / Review). Trace explicitement où je tranche vs où je laisse
   l'outil proposer.
3. **[`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) §13 bis**
   — chronologie d'implémentation post-MVP (S09.5 → S10) avec
   triggers et arbitrages.

## Tester

Parcours **5 min** complet pour évaluateur :
[`EVALUATION.md`](EVALUATION.md).

Smoke test post-deploy local :

```bash
bash scripts/smoke_S09.sh        # 3 curl + jq
make test                        # unit only (gratuit)
make test-integration            # live, opt-in (consomme crédits)
```

## Chronologie d'implémentation (en 1 minute)

Le projet est découpé en 10 stories verticales. Chaque story a son
propre fichier dans `docs/stories/` avec : phase 1 d'élicitation
(raffinage avant code, vérif des SDK 2026), phase 2 dev, phase 3
review, et un journal des hotfixes appliqués en live. Lecture rapide
recommandée pour suivre le cheminement décisionnel :

1. **S01 → S03 (samedi matin, MVP)** — scaffold `uv` + `ruff`,
   client MCP Pappers streamable-http (31 tools exposés, filtrage à
   ceux utiles aux cas U1–U3), agent Claude avec tool-use natif.
2. **S04 → S06 (samedi après-midi)** — routing Haiku ↔ Sonnet 3-couches
   (keyword + auto-escalade + cap dur), 6 couches de garde-fous, UI
   Chainlit avec steps tool ouverts.
3. **S07 → S08 (samedi soir)** — `structlog` JSON + `/health` +
   `/stats` Bearer-gated, Dockerfile + Railway EU-West Amsterdam +
   keep-alive UptimeRobot keyword monitor.
4. **S09 (dimanche matin, polish + dogfooding)** — README + EVALUATION
   + pack adversarial 10 prompts. **Le dogfooding révèle un problème
   produit** : sur U3 l'agent répond avec les bilans 2016 au lieu de
   2024. Cause : payloads Pappers > 700 K chars sur Carrefour Hyper,
   troncature S05 coupe avant les bilans récents.
5. **S09.5 (rouverte dimanche après-midi)** — **Payload Vault** :
   après comparaison de 5 patterns (programmatic tool calling, Deep
   Agents filesystem, sub-agent synthesizer, wrapper per-tool,
   hybride), choix d'un offload générique session-scoped + 2 tools
   locaux ``payload_inspect`` / ``payload_search``. L'agent ré-interroge
   un index JSON compact, raisonnement métier inchangé. Carrefour
   passe verbatim au CA 2024.
6. **S09.6 (dimanche soir)** — **mitigation Pappers PAYG**. Pendant
   les retests, ``comptes-entreprise`` se met à refuser les PAYG par
   intermittence (bug serveur Pappers). Cache disque baked + volume
   Railway → 4 entités golden × 3 ans pré-warmées hors crédits.
   Persistance SQLite des conversations Chainlit (sidebar threads
   cross-session).
7. **S09.7 (dimanche soir)** — **robustesse extraction + cap UX**.
   jsonpath-ng wildcards, prompt caching Anthropic
   (``cache_control: ephemeral`` → bump tokens 80 K → 200 K),
   cap-as-UX-event (« 🔄 Continuer » / « 📋 Synthèse partielle » au
   lieu d'un dead-end), auto-continuation Vault. 18 hotfixes UI/UX
   appliqués live (FOUC, splash, owner_id cookie, anti-zigzag).
8. **S10 (lundi, stretch voice)** — pivot du brief vocal v1
   vers un **voice mode conversationnel** Eleven Agents (custom LLM
   SSE + ASR + turn-taking + TTS streaming). L'agent reste 100 %
   inchangé côté logique ; voice mode est une couche I/O wrapper.
   POC end-to-end validé 2026-04-27.

> Volontairement, **les stories sont des journaux de bord plutôt que
> des post-mortems aseptisés** : on y trouve les hypothèses testées,
> les hotfixes, les gotchas (ex : ElevenLabs append automatiquement
> `/chat/completions` à l'URL custom LLM, cf.
> [`docs/deployment.md`](docs/deployment.md) §3 ter). C'est plus lisible
> qu'un post-mortem reformaté après coup et permet à un lecteur tiers
> de juger non seulement le résultat mais aussi le processus.

## Architecture

Cf. [`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) §5.

```
Utilisateur (FR)
    │
    ▼ HTTPS
Chainlit UI (Railway EU-West, Amsterdam)
    │
    ▼
Pipeline garde-fous (run_guarded_turn) ── 6 couches §14.3
    │
    ▼
Router keyword + Haiku 4.5 (auto-escalade) ↔ Sonnet 4.6
    │
    ▼
MCP client streamable-http
    │
    ▼
mcp.pappers.fr/{API_KEY}
```

## Limites externes connues et mitigations livrées

Deux contraintes du côté Pappers / Chainlit qui se manifestent en prod
et que le repo documente + mitige plutôt que de les masquer :

- **Comptes annuels multi-années Pappers** : le tool ``comptes-entreprise``
  refuse par intermittence les jetons Pay-As-You-Go (bug serveur
  Pappers identifié 2026-04-25, ticket ouvert côté Pappers).
  Mitigations livrées en **S09.6** :
  - **Cache disque baked dans Docker** (``data/mcp_cache.json``) +
    volume persistant Railway (``/data``) → 4 entités golden
    × 3 années pré-warmées, servies depuis le cache (TTL 7 j) sans
    appel live. La démo U3 ne dépend plus du solde abo Pappers.
  - **Pre-warm manuel** via `make prewarm-comptes` au refill du pack
    abonnement (rejouable avant chaque démo).
  - **Fallback automatique côté agent** : si cache miss + abo épuisé,
    le tool MCP retourne un ``workaround_hint`` qui dirige
    l'agent vers ``recherche-entreprises`` (CA / résultat headline
    en 1 crédit PAYG) ou un refus poli sourcé.
  - Détail dans [`docs/pappers-mcp.md`](docs/pappers-mcp.md) §4.2 + §4.3.
- **Persistance des conversations Chainlit** : data layer SQLite
  ``data/cl_threads.db`` (S09.6) → la sidebar liste les conversations
  précédentes et résume au clic. L'historique survit aux redémarrages
  serveur tant que le volume Railway ``/data`` est intact ; un rebuild
  Docker qui repart du bake écrase l'historique runtime — acceptable
  pour le scope démo, à promouvoir Postgres en cas de scale-out.

## Sécurité & robustesse

- **6 couches** garde-fous : input gate (regex anti-injection 2026 sur
  texte normalisé NFKD), system prompt durci, Claude safety native,
  execution caps (7 tool calls Pappers / 10 lookups locaux Payload Vault
  / 60 s wall-clock / 200 K tokens-per-session), output validator
  déterministe (Luhn SIREN + bilan horodaté + advisory reframing),
  Haiku-critic async non-bloquant.
- **Cap-as-UX-event** (S09.7) : tout cap firefired émet un event
  `cap_continuation_proposed` qui expose dans la UI Chainlit les
  actions « 🔄 Continuer » / « 📋 Synthèse partielle » plutôt qu'un
  dead-end conversationnel. Le contexte (Payload Vault inclus) est
  préservé sur le `ConversationState` session-scoped.
- **Pack adversarial 10 prompts** exécutés automatiquement,
  rapport : [`docs/adversarial-run.md`](docs/adversarial-run.md)
  (généré par `tests/integration/test_S09_adversarial.py`).
- **Secrets** jamais commités (`gitleaks` en pre-commit + en CI),
  URL MCP Pappers jamais loguée (server-only par construction).
- `/stats` auth-gate Bearer en prod Railway (`STATS_TOKEN` env var,
  comparaison `hmac.compare_digest`).

## Next steps (si prod)

1. ~~**Anthropic prompt caching**~~ — **livré dans S09.7**
   (`cache_control: ephemeral` sur tools + system + messages[-1]
   dans [`agent.py:run_turn`](src/genial_agent/agent.py)). Couple
   les compteurs `anthropic_cache_creation_tokens` /
   `anthropic_cache_read_tokens` exposés dans `/stats` pour mesurer
   le ROI en continu. Permet le bump `MAX_TOKENS_PER_SESSION`
   80 K → 200 K (s'aligne sur la context window Sonnet 4.6) sans
   exploser la facturation. À noter : `T9_lang_chinese` reste dans
   `TOLERATED` malgré le caching — la cause racine était un comportement
   Sonnet (boucle "Je vais d'abord rechercher") indépendant du TTFT.
2. ~~**Slicing intelligent `comptes-entreprise`**~~ — **livré dans
   S09.5** ([`payload_vault.py`](src/genial_agent/payload_vault.py) +
   tools locaux `payload_inspect` / `payload_search`). Offload
   générique session-scoped : tout payload MCP > 12 K chars est
   rangé dans un vault in-memory, l'agent reçoit un index JSON
   compact et ré-interroge à la demande. Sur Carrefour
   Hypermarchés (706 K chars), l'agent récupère désormais le CA
   2024 verbatim au lieu de tronquer à 2016. Cf. story
   [`S09.5`](docs/stories/S09.5-mcp-payload-handling.md) et tableau
   avant/après [`docs/inspection-mcp-vs-agent.md`](docs/inspection-mcp-vs-agent.md)
   §"Après S09.5".
3. **Bascule Bedrock EU** (Paris) ou **Vertex AI EU** (Frankfurt)
   pour résidence RGPD — `anthropic[bedrock]`, ~20 lignes.
4. **Tracing distribué Langfuse / OpenTelemetry** — 1 trace par
   `run_guarded_turn`, 1 span par `llm_meta`. ~1 h.
5. **Audit trail append-only** (S3 + manifest signé) — exigence type
   SOC 2 pour Cegid / Crédit Agricole. ~2 h.
6. **Custom domain** `genial-agent.lancelotoudin.fr` — DNS + cert
   Railway. ~10 min.
7. **Path filtering Railway repoTriggers** pour économiser les
   redeploys doc-only (cf. `docs/deployment.md` annexe). ~5 min via
   GraphQL.
8. ~~**Brief vocal ElevenLabs** (S10)~~ — **livré en S10 sous forme de
   voice mode conversationnel duplex Eleven Agents** (custom LLM SSE +
   ASR + TTS streaming + narration tool steps). POC end-to-end validé
   2026-04-27 sur l'API ``simulate-conversation`` ElevenLabs. Pivot
   acté après lecture détaillée de la doc Eleven Agents : pour le
   même budget (~3 $ sur le week-end) et sans toucher la logique
   agent, on passe d'un brief radio TTS post-réponse à un vrai
   chat-vocal style ChatGPT Voice. Activable à chaud via
   `ENABLE_VOICE_MODE=true` côté Railway (cf.
   [`docs/deployment.md`](docs/deployment.md) §3 ter). Désactivé par
   défaut côté prod pour cadrer la consommation de minutes Eleven
   Agents (tier `growing_business`) — le chat texte reste 100 %
   fonctionnel et constitue le chemin nominal de l'évaluation.

## Licence

MIT.
