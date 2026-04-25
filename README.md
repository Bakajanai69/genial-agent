# genial-agent

Agent IA spécialisé sur les entreprises françaises, branché sur le
**MCP Pappers** (streamable-http). Construit dans le cadre d'un
exercice d'évaluation AI Builder (week-end, ~12 h).

🔗 **Démo live** : <https://genial-agent-production.up.railway.app>
🎬 **Loom 2 min** : <https://www.loom.com/share/<id-loom>>
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
#   testées E2E par le boot — un /health KO indique une clé invalide).
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

## Sécurité & robustesse

- **6 couches** garde-fous : input gate (regex anti-injection 2026 sur
  texte normalisé NFKD), system prompt durci, Claude safety native,
  execution caps (7 tool calls / 60 s wall-clock / 80 K tokens-per-
  session), output validator déterministe (Luhn SIREN + bilan
  horodaté + advisory reframing), Haiku-critic async non-bloquant.
- **Pack adversarial 10 prompts** exécutés automatiquement,
  rapport : [`docs/adversarial-run.md`](docs/adversarial-run.md)
  (généré par `tests/integration/test_S09_adversarial.py`).
- **Secrets** jamais commités (`gitleaks` en pre-commit + en CI),
  URL MCP Pappers jamais loguée (server-only par construction).
- `/stats` auth-gate Bearer en prod Railway (`STATS_TOKEN` env var,
  comparaison `hmac.compare_digest`).

## Next steps (si prod)

1. **Anthropic prompt caching** (`cache_control` sur `system` +
   `tools` + dernier `messages` block) — coupe TTFT 5-10× et
   ramène le wall-clock cap S04 de 60 s à 30 s. Effort ~1 h.
   Vrai fix produit du flap `WALL_CLOCK_S 30→60 s` noté en
   review S08 §B1bis.
1bis. **Slicing intelligent `comptes-entreprise`** — au lieu de
   la borne aveugle 16 K chars (`agent.py:_TOOL_RESULT_MAX_CHARS`)
   qui coupe les bilans Pappers très volumineux (706 K chars sur
   Carrefour Hypermarchés sans `annee`), extraire les sections
   clés (CA, résultat net, total actif, effectif moyen) sur la
   dernière année + 2 précédentes. Découverte du dogfooding S09
   inspection MCP, cf.
   [`docs/inspection-mcp-vs-agent.md`](docs/inspection-mcp-vs-agent.md).
   Effort ~2 h.
2. **Bascule Bedrock EU** (Paris) ou **Vertex AI EU** (Frankfurt)
   pour résidence RGPD — `anthropic[bedrock]`, ~20 lignes.
3. **Tracing distribué Langfuse / OpenTelemetry** — 1 trace par
   `run_guarded_turn`, 1 span par `llm_meta`. ~1 h.
4. **Audit trail append-only** (S3 + manifest signé) — exigence type
   SOC 2 pour Cegid / Crédit Agricole. ~2 h.
5. **Custom domain** `genial-agent.lancelotoudin.fr` — DNS + cert
   Railway. ~10 min.
6. **Path filtering Railway repoTriggers** pour économiser les
   redeploys doc-only (cf. `docs/deployment.md` annexe). ~5 min via
   GraphQL.
7. **Brief vocal ElevenLabs** (S10) — déclenché si gating §19.1 vert.

## Licence

MIT.
