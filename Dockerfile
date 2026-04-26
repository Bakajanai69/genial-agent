# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.12

# ─── Stage builder ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
WORKDIR /app

# uv via image officielle Astral (évite pip install + cache miss).
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

# Layer cache : pyproject.toml + uv.lock + README.md (hatchling lit
# le readme au build — si .dockerignore l'exclut, COPY échoue).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Sync sans dev deps, sans editable install (image runtime plus légère).
RUN uv sync --frozen --no-dev --no-editable

# ─── Stage runtime ──────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm

# curl requis par la directive HEALTHCHECK ci-dessous (slim-bookworm
# ne l'embarque pas). --no-install-recommends + rm des apt lists pour
# garder l'image fine.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 agent

USER agent
WORKDIR /app

# venv + sources + assets Chainlit (config.toml S06, chainlit.md UI,
# public/ pour le footer.css).
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent src ./src
COPY --chown=agent:agent chainlit.md ./
COPY --chown=agent:agent public ./public
COPY --chown=agent:agent .chainlit ./.chainlit

# S09.6 — Bootstrap des caches (mcp_cache.json + cl_threads.db) baked
# dans l'image. Au boot Railway, src/genial_agent/data_bootstrap.py
# copie ces fichiers vers le volume persistant /data si vide. Les
# données Pappers cachées sont publiques (raisons sociales, bilans,
# SIREN) — pas de secret, pas de PII. Cf. story S09.6 §"Architecture
# phase 1 — Axe 3 révisé".
COPY --chown=agent:agent data ./data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# EXPOSE déclaratif pour `docker run -p 8000:8000`. Railway ignore
# cette directive et bind sur le PORT injecté (8080 par défaut).
EXPOSE 8000

# HEALTHCHECK = info-only en local (`docker ps` montre healthy/unhealthy).
# Railway utilise son propre healthcheck via railway.json (healthcheckPath).
# --start-period=15s : laisse le temps au boot Chainlit + 1er ping
# Pappers d'aboutir avant la 1ère retry. À ré-évaluer si on ajoute des
# handshakes au boot (S10 ElevenLabs).
# Q6 review S08 : on absorbe stdout ET stderr (``2>&1``) — sans ça,
# curl spam stderr lors d'un DNS fail / refused au démarrage.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -fsS "http://localhost:${PORT:-8000}/health" >/dev/null 2>&1 || exit 1

# Shell-form CMD : ${PORT} expansé par sh. Avec exec-form, Chainlit
# recevrait littéralement la chaîne "${PORT}" et crasherait.
# Flag -h : empêche Chainlit d'ouvrir un browser côté serveur en prod
# (cf. docs.chainlit.io/deploy/overview).
CMD ["sh", "-c", "chainlit run src/genial_agent/app.py -h --host 0.0.0.0 --port ${PORT:-8000}"]
