# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder
WORKDIR /app
# uv binary via image officielle Astral — évite pip install overhead
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM python:3.12-slim-bookworm
RUN useradd --create-home --uid 1000 agent
USER agent
WORKDIR /app
COPY --from=builder --chown=agent:agent /app/.venv /app/.venv
COPY --chown=agent:agent src ./src
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
# NB : `src/genial_agent/app.py` n'existe qu'à partir de S06 ; pour
# l'étape S01 l'image build mais `docker run` échouera au démarrage
# (comportement attendu).
CMD ["chainlit", "run", "src/genial_agent/app.py", "--host", "0.0.0.0", "--port", "8000"]
