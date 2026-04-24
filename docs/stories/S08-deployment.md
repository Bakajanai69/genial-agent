# S08 — Déploiement Railway + keep-alive UptimeRobot

> **Statut** : ⬜ à faire
> **Durée estimée** : 45 min
> **Parallélisable avec** : S07

---

## 📍 Contexte

Mettre l'agent en ligne sur une URL publique HTTPS. Railway EU-West
(Amsterdam), keep-alive UptimeRobot pour éviter le cold start pendant
le week-end d'évaluation.

Sources de vérité :
- `docs/cahier-des-charges.md` §6.3, §17.1 (keep-alive), §17.5 (plan B).

---

## 🔒 Prérequis

- [ ] S01 (Dockerfile) terminée.
- [ ] S07 idéalement (pour avoir `/health` ping-able par UptimeRobot).

## 🔑 Inputs utilisateur requis

- [ ] Compte Railway créé (GitHub SSO OK).
- [ ] Projet Railway créé et lié au repo `Bakajanai69/genial-agent`,
      région Amsterdam.
- [ ] Variables d'env à **recopier** dans Railway Project Variables
      depuis le `.env` local (déjà validées en local avant S08, cf.
      `docs/stories/README.md` → check-list) :
  - `ANTHROPIC_API_KEY` (testée OK sur Haiku 4.5 + Sonnet 4.6).
  - `PAPPERS_API_KEY` (handshake MCP OK, 31 tools exposés).
  - `ELEVENLABS_API_KEY` (tier `growing_business`, quota large).
  - `ELEVENLABS_VOICE_GAELLE`, `ELEVENLABS_VOICE_GUILLAUME`,
    `ELEVENLABS_MODEL_ID` (constantes publiques).
  - `ENABLE_VOICE_BRIEF=false` (à flipper `true` **après** merge S10 +
    gating §19.1 vert).
  - `LOG_LEVEL=INFO`.
- [ ] Compte UptimeRobot créé (plan free).

> ⚠️ **Ne jamais coller les clés dans un canal Railway public** (issue,
> PR, log). Les ajouter uniquement via le panel Variables (masquées par
> défaut).

---

## 🎯 Scope

### Dans le scope

- Finalisation du `Dockerfile` (multi-stage, slim, non-root, healthcheck).
- Fichier `railway.json` (ou `railway.toml`) avec la config service.
- Build du Docker localement pour valider avant push.
- Déploiement manuel initial via Railway dashboard ou CLI.
- URL publique obtenue, testée en HTTPS.
- UptimeRobot : monitor HTTP(s) sur `https://<domain>/health` ping 5 min.
- Instructions de déploiement documentées dans `docs/deployment.md` (pour
  qu'un dev tiers puisse relancer).

### Hors scope

- CI/CD auto-deploy (pour l'exo, manual OK).
- Multi-région, load balancing.

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Syntaxe actuelle du fichier de config Railway (`railway.json` vs
      `railway.toml`), format 2026.
- [ ] Comment spécifier la région EU-West (Amsterdam) via config.
- [ ] Command de healthcheck Docker `HEALTHCHECK CMD` compatible
      Railway.
- [ ] Railway exige le port via `PORT` env var (par défaut souvent
      8080) — à confirmer.
- [ ] UptimeRobot : si Railway retourne 401 sur `/health` (ce qui ne
      devrait pas être le cas), documenter comment passer un token.
- [ ] Taille d'image Docker cible (idéalement < 400 MB pour démarrage
      rapide).

### Points à résoudre

- [ ] Slim vs Alpine : stick with `python:3.12-slim-bookworm` pour
      compat des wheels binaires.
- [ ] Non-root user : créer `agent` en UID 1000.

### Commit phase 1

`story(S08): refine — railway.json 2026, Dockerfile final, healthcheck`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer / modifier

- `Dockerfile` (finalisation).
- `railway.json` (ou `.toml` selon phase 1).
- `docs/deployment.md`.
- `.dockerignore` (si manquant).

### `Dockerfile` final

```dockerfile
# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 agent

USER agent
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent src ./src
COPY --chown=agent:agent chainlit.md* ./
COPY --chown=agent:agent public ./public

ENV PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT}/health" || exit 1

CMD ["sh", "-c", "chainlit run src/genial_agent/app.py --host 0.0.0.0 --port ${PORT}"]
```

### `railway.json`

À ajuster en phase 1 selon la version courante du schéma Railway :

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "region": "europe-west",
    "startCommand": "chainlit run src/genial_agent/app.py --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/health",
    "healthcheckTimeout": 30,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 3
  }
}
```

### `.dockerignore`

```
.git/
.venv/
.env
.env.*
__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.mypy_cache/
.chainlit/
docs/
tests/
*.md
!README.md
!chainlit.md
```

### `docs/deployment.md`

Contenu attendu :
1. Procédure création projet Railway.
2. Liste exhaustive des env vars à ajouter.
3. Configuration région Amsterdam.
4. Trigger first deploy (push sur main, ou manual).
5. Récupération de l'URL publique.
6. Configuration UptimeRobot (URL, intervalle, alerting).
7. Troubleshooting : common errors (port mismatch, missing env, Docker
   build failure).

### Tests à produire

#### Build test

```bash
# Local, avant push
make docker-build
docker run --rm -p 8000:8000 \
    -e ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
    -e PAPPERS_API_KEY=$PAPPERS_API_KEY \
    genial-agent:local &
sleep 5
curl -f http://localhost:8000/health
```

Test scripté : un `tests/integration/test_S08_docker.py` qui skip si
Docker absent, sinon build et lance un health check. (optionnel, peut
être juste un script bash si pytest n'est pas adapté).

#### Smoke test post-déploiement

```bash
# Une fois l'URL publique obtenue
curl -fsS https://<railway-domain>/health
# Doit retourner {"status": "ok", ...}
```

Documenté dans `docs/deployment.md`.

### Commit phase 2

`feat(S08): Dockerfile + railway.json + deployment docs`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] Dockerfile utilise un user non-root.
- [ ] Image Docker < 500 MB (`docker images | grep genial-agent`).
- [ ] Aucune secret en ARG ou ENV dans le Dockerfile.
- [ ] `.dockerignore` couvre `.env`, `.venv`, `__pycache__`.
- [ ] `railway.json` pointe vers la bonne région EU.
- [ ] `/health` accessible sur l'URL publique.
- [ ] UptimeRobot configuré et le monitor est en statut "up".
- [ ] `docs/deployment.md` permet à un nouveau contributeur de relancer
      le déploiement de zéro.

### Commit phase 3

`review(S08): approved`

---

## ✅ Critères d'acceptation

- [ ] `make docker-build` passe localement.
- [ ] Conteneur local répond sur `http://localhost:8000/health` avec 200.
- [ ] URL publique Railway répond en HTTPS avec 200 sur `/health`.
- [ ] UptimeRobot affiche "up" et intervalle 5 min actif.
- [ ] `gitleaks` clean sur le repo.

---

## 📦 Done when

- [ ] Phase 1 commitée.
- [ ] Phase 2 commitée + build + déploiement effectué.
- [ ] Phase 3 approuvée.
- [ ] URL publique notée dans `docs/deployment.md` et dans
      `docs/stories/README.md`.
- [ ] Ligne S08 mise à jour → ✅.
- [ ] Push effectué.
