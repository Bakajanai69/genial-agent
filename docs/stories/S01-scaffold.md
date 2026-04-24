# S01 — Scaffold du repo

> **Statut** : ⬜ à faire
> **Durée estimée** : 45 min
> **Parallélisable avec** : — (fondation)

---

## 📍 Contexte

Première story. Elle pose les fondations techniques du projet : outillage
Python, structure de dossiers, linting, tests, CI, Dockerfile squelette,
pre-commit. Toutes les stories suivantes reposent dessus.

Sources de vérité :
- `docs/cahier-des-charges.md` §5.1 (stack technique), §10 (livrables).

Résultat attendu : on peut cloner, `make install`, `make lint`, `make test`
— tout passe (avec 0 test pour l'instant, c'est ok).

---

## 🔒 Prérequis

- [ ] Repo cloné localement sur la branche `claude/builder-evaluation-exercise-34Iyu`.

## 🔑 Inputs utilisateur requis

- [ ] Python 3.11 ou 3.12 installé (`python --version`).
- [ ] `uv` installé (`pip install uv` ou installer officiel).
- [ ] Docker installé (pour tester l'image localement plus tard).

---

## 🎯 Scope

### Dans le scope

- `pyproject.toml` avec `uv`, deps de base (anthropic, mcp, chainlit,
  structlog, pydantic, pytest, ruff, pre-commit).
- `.python-version` pinnant la version choisie.
- `.gitignore` strict.
- `.env.example` avec toutes les variables attendues (clés à venir).
- `Makefile` (install, run, test, lint, format, docker-build).
- `Dockerfile` squelette multi-stage (slim, non-root).
- `README.md` skeleton (rempli en S09).
- Structure `src/genial_agent/` + `tests/unit/` + `tests/integration/`.
- `pytest.ini` ou `[tool.pytest.ini_options]` dans `pyproject.toml`.
- `ruff` configuré (règles strictes raisonnables).
- `.pre-commit-config.yaml` avec `gitleaks` + `ruff`.
- `.github/workflows/ci.yml` minimal : lint + test sur push.

### Hors scope

- Code agent (S03+).
- Client MCP (S02).
- UI Chainlit (S06).
- Dockerfile finalisé pour prod (S08).

---

## 🧭 Phase 1 — Elicitation Agent

### Recherche en ligne à effectuer

- [ ] Vérifier la dernière version stable de `uv` et la syntaxe
      recommandée pour `pyproject.toml` en 2026.
- [ ] Confirmer Python 3.12 ou 3.13 (choix à figer : recommandation 3.12
      pour compat Chainlit et Claude Agent SDK).
- [ ] Versions actuelles de : `anthropic`, `mcp`, `chainlit`, `structlog`,
      `pydantic` (v2), `pytest`, `ruff`, `tenacity`, `python-dotenv`.
- [ ] Dernière version action GitHub `zricethezav/gitleaks-action`.
- [ ] Règles `ruff` courantes en 2026 (pycodestyle, pyflakes, isort,
      bugbear, simplify, security).
- [ ] Docker : base `python:3.12-slim-bookworm` ou équivalent, pattern
      non-root, copy-then-install pour caching optimal.

### Variables `.env.example` à lister

Noms exacts (valeurs vides) — à confirmer / étendre lors des stories
suivantes :

```bash
# Anthropic
ANTHROPIC_API_KEY=

# Pappers MCP
PAPPERS_API_KEY=

# ElevenLabs (optionnel, stretch S10)
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD
ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

# Feature flags
ENABLE_VOICE_BRIEF=false

# Observabilité
LOG_LEVEL=INFO
```

### Points à résoudre

- [ ] Python 3.12 vs 3.13 : figer dans `.python-version`.
- [ ] Inclure `mypy` ou pas ? Recommandation : **non** pour l'exo, `ruff`
      + Pydantic runtime valide suffit.
- [ ] Layout `src/` vs layout plat : **src/** retenu (standard packaging
      moderne).

### Commit phase 1

`story(S01): refine — uv 2026, python 3.12, deps pinned`

---

## 🛠 Phase 2 — Dev Agent

### Fichiers à créer

```
.
├── .github/
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── .pre-commit-config.yaml
├── .python-version
├── .env.example
├── Dockerfile
├── Makefile
├── README.md          # skeleton minimal, rempli en S09
├── pyproject.toml
├── src/
│   └── genial_agent/
│       └── __init__.py
└── tests/
    ├── __init__.py
    ├── unit/
    │   └── __init__.py
    └── integration/
        └── __init__.py
```

### Contenu clés

#### `pyproject.toml`

```toml
[project]
name = "genial-agent"
version = "0.1.0"
description = "Agent IA spécialisé entreprises françaises via MCP Pappers"
requires-python = ">=3.12"
readme = "README.md"
dependencies = [
    "anthropic>=<version à figer en phase 1>",
    "mcp>=<version>",
    "chainlit>=<version>",
    "structlog>=<version>",
    "pydantic>=2",
    "python-dotenv>=1",
    "tenacity>=<version>",
]

[project.optional-dependencies]
dev = [
    "pytest>=<version>",
    "pytest-asyncio>=<version>",
    "pytest-cov>=<version>",
    "ruff>=<version>",
    "pre-commit>=<version>",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra --strict-markers"
markers = [
    "integration: requires real API keys",
]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "SIM", "UP", "S"]
ignore = ["S101"]  # assert OK dans les tests

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S"]  # pas de checks sécurité dans les tests
```

#### `.gitignore`

Classique Python + `.env`, `.venv/`, `.ruff_cache/`, `.pytest_cache/`,
`.chainlit/`, `.files/`, `*.egg-info/`, `__pycache__/`, `dist/`,
`build/`, `.coverage`, `htmlcov/`.

#### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: <version à figer phase 1>
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/gitleaks/gitleaks
    rev: <version à figer phase 1>
    hooks:
      - id: gitleaks
```

#### `Makefile`

```makefile
.PHONY: install run test lint format docker-build precommit

install:
	uv sync --extra dev
	uv run pre-commit install

run:
	uv run chainlit run src/genial_agent/app.py -w

test:
	uv run pytest tests/unit -v
	uv run pytest tests/integration -v --tb=short || true

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

lint:
	uv run ruff check src tests
	uv run ruff format --check src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests

docker-build:
	docker build -t genial-agent:local .

precommit:
	uv run pre-commit run --all-files
```

#### `Dockerfile` (squelette, finalisation en S08)

```dockerfile
# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

FROM python:3.12-slim-bookworm
RUN useradd -m -u 1000 agent
USER agent
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --chown=agent:agent src ./src
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["chainlit", "run", "src/genial_agent/app.py", "--host", "0.0.0.0", "--port", "8000"]
```

#### `.github/workflows/ci.yml`

```yaml
name: CI
on:
  push:
    branches: ["**"]
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - name: Install
        run: uv sync --extra dev
      - name: Lint
        run: uv run ruff check src tests
      - name: Format check
        run: uv run ruff format --check src tests
      - name: Unit tests
        run: uv run pytest tests/unit -v
```

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S01_scaffold.py
def test_package_importable():
    import genial_agent
    assert genial_agent.__name__ == "genial_agent"
```

Un seul test smoke suffit pour cette story. Les vrais tests viennent avec
les stories suivantes.

### Commandes de vérification

```bash
make install   # doit installer sans erreur
make lint      # doit être clean
make test      # doit passer (1 test smoke)
make docker-build  # doit build sans erreur
uv run pre-commit run --all-files  # doit passer
```

### Commit phase 2

`feat(S01): scaffold repo with uv, ruff, pre-commit, docker, CI`

---

## 🔍 Phase 3 — Review Agent

### Check-list spécifique

- [ ] `pyproject.toml` toutes les versions sont **pinnées** (pas de `*`
      ou `>=`).
- [ ] `.gitignore` couvre bien `.env`, `.venv`, `.chainlit`.
- [ ] `.env.example` n'a aucune valeur réelle (pour les voice IDs c'est OK,
      ce sont des constantes publiques).
- [ ] `Dockerfile` utilise un user non-root.
- [ ] CI GitHub Actions s'exécute et passe (vérifier via l'onglet Actions
      sur GitHub).
- [ ] `make install` fonctionne depuis un clone frais.
- [ ] Pas de TODO oublié dans les fichiers.

### Commit phase 3

`review(S01): approved`

---

## ✅ Critères d'acceptation

- [ ] `make install` retourne 0.
- [ ] `make lint` retourne 0.
- [ ] `make test` retourne 0 avec 1 test passant.
- [ ] `make docker-build` retourne 0.
- [ ] `pre-commit run --all-files` retourne 0.
- [ ] CI GitHub est verte sur le commit de phase 2.
- [ ] `gitleaks detect` ne trouve rien.

---

## 📦 Done when

- [ ] Phase 1 commitée (`story(S01): refine`).
- [ ] Phase 2 commitée (`feat(S01): scaffold`) + tests verts.
- [ ] Phase 3 approuvée (`review(S01): approved`).
- [ ] Ligne S01 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push sur `claude/builder-evaluation-exercise-34Iyu`.
