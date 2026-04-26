# genial-agent

Agent IA spécialisé sur les entreprises françaises, branché sur le
**MCP Pappers** (streamable-http). Construit dans le cadre d'un
exercice d'évaluation AI Builder (week-end, ~12 h).

🔗 **Démo live** : <https://genial-agent-production.up.railway.app>
🎬 **Loom 2 min** : `https://www.loom.com/share/<id-loom>` *(à remplacer après enregistrement)*
📦 **Repo** : <https://github.com/Bakajanai69/genial-agent>

[![CI](https://github.com/Bakajanai69/genial-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Bakajanai69/genial-agent/actions/workflows/ci.yml)
[![service](https://img.shields.io/website?url=https%3A%2F%2Fgenial-agent-production.up.railway.app%2Fhealth&up_message=online&down_message=offline&label=service)](https://genial-agent-production.up.railway.app/health)

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

## Tester

Parcours **5 min** complet pour évaluateur :
[`EVALUATION.md`](EVALUATION.md).

Smoke test post-deploy local :

```bash
bash scripts/smoke_S09.sh        # 3 curl + jq
make test                        # unit only (gratuit)
make test-integration            # live, opt-in (consomme crédits)
```

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

## Limites connues (S09.6)

- **Comptes annuels multi-années Pappers** : le tool ``comptes-entreprise``
  refuse parfois les jetons Pay-As-You-Go (bug serveur Pappers, ticket
  ouvert 2026-04-25). Mitigation S09.6 :
  - Cache disque baked dans Docker (``data/mcp_cache.json``) +
    volume persistant Railway (``/data``) → les 4 entités golden
    × 3 années sont servies depuis le cache (TTL 7j) sans appel live.
  - Pre-warm manuel mensuel via `make prewarm-comptes` au refill du
    pack abonnement.
  - Fallback automatique côté agent : si cache miss + abo épuisé, le
    tool retourne un ``workaround_hint`` qui dirige Claude vers
    ``recherche-entreprises`` (CA / résultat headline en 1 crédit
    PAYG) ou un refus poli sourcé.
  - Détail dans [`docs/pappers-mcp.md`](docs/pappers-mcp.md) §4.2 + §4.3.
- **Conversation Chainlit "fini"** : la sidebar liste les
  conversations précédentes (data layer SQLite ``data/cl_threads.db``).
  Persiste tant que le volume Railway est intact ; un rebuild Docker
  qui repart du bake écrase l'historique runtime — acceptable pour la
  démo.

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
8. **Brief vocal ElevenLabs** (S10) — déclenché si gating §19.1 vert.

## Licence

MIT.
