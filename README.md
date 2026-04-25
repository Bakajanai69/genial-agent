# genial-agent

Agent IA spécialisé sur les entreprises françaises via MCP Pappers.

> **Statut** : scaffold posé (S01). README complet en S09.

## Démarrage rapide

```bash
make install   # installe les deps via uv + git hooks pre-commit
make lint      # ruff check + format
make test      # pytest
make run       # lance Chainlit (à partir de S06)
```

## Stack technique

- **Python 3.12** + **uv** pour la gestion de deps.
- **Anthropic SDK** (`anthropic`) + **MCP SDK** (`mcp`) pour l'agent
  Claude avec tool use sur MCP Pappers en streamable-http.
  > ℹ️ Clarification vs cahier §5.1 : l'expression "Claude Agent SDK"
  > du cahier désigne la combinaison `anthropic` + `mcp` côté Python
  > (le package PyPI `claude-agent-sdk` est un wrapper du CLI Claude
  > Code, pas adapté ici). Détail dans `docs/stories/S01-scaffold.md`.
- **Chainlit** pour l'UI chat avec streaming + step view des tool calls.
- **Pydantic v2** pour la validation de sortie.
- **structlog** pour les logs JSON.

Voir :

- [`docs/cahier-des-charges.md`](docs/cahier-des-charges.md) — spec produit.
- [`docs/pappers-mcp.md`](docs/pappers-mcp.md) — garde-fous techniques Pappers.
- [`docs/stories/`](docs/stories/) — découpage en stories verticales.

## Configuration

Copier `.env.example` → `.env` et remplir les clés API
(`ANTHROPIC_API_KEY`, `PAPPERS_API_KEY`, et optionnellement
`ELEVENLABS_API_KEY` pour le stretch vocal S10).

## Déploiement

L'agent tourne en prod sur **Railway EU-West (Amsterdam)** :

🌐 **URL publique** : <https://genial-agent-production.up.railway.app>
(`/health` → JSON status:ok · `/stats` → compteurs S07 · `/` → UI Chainlit)

Config-as-code dans `railway.json` (région, healthcheck, restart).
Procédure complète + troubleshooting dans
[`docs/deployment.md`](docs/deployment.md).

Keep-alive **UptimeRobot** (plan Free) ping `/health` toutes les
5 min en mode keyword `"status":"ok"` pour empêcher le cold start
et alerter sur panne MCP Pappers pendant le week-end d'évaluation.

## Livraison

Repo : `github.com/Bakajanai69/genial-agent`
Branche courante : `claude/builder-evaluation-exercise-34Iyu`
