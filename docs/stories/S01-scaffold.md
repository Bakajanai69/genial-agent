# S01 — Scaffold du repo

> **Statut** : ✅ approved (phase 3 terminée 2026-04-24)
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

- [x] Repo cloné localement sur la branche `claude/builder-evaluation-exercise-34Iyu`.
- [x] `.env` à la racine **déjà rempli** avec les 3 clés API
      (ANTHROPIC, PAPPERS, ELEVENLABS) et gitignoré — cf. check-list
      globale dans `docs/stories/README.md`.
- [x] `.gitignore` racine déjà présent (a été créé pré-S01 pour
      protéger le `.env`, cf. commit `a35a24e`). À ne pas écraser
      lors de la phase 2 : le compléter si besoin, ne pas le recréer.

## 🔑 Inputs utilisateur requis

- [x] Python 3.12 installé — **3.12.3 détecté** localement (`/usr/bin/python3`).
      Décision : on reste sur **3.12** (installation locale déjà en place,
      Chainlit 2.11.1 + toutes les deps compatibles, `python:3.12-slim-bookworm`
      dispo en image officielle).
- [x] `uv` installé — **0.10.6 détecté** en `/home/lancelot/.local/bin/uv`.
      (dernier release stable PyPI `0.11.7` — pas besoin de mettre à jour pour
      le scaffold, on pinnera via `setup-uv@v8` côté CI).
- [x] Docker installé — **27.4.0 détecté**.

> ℹ️ Les 3 clés API sont **déjà provisionnées** dans `.env`. La phase 2
> crée `.env.example` (valeurs vides, committé) en miroir du `.env`
> mais ne touche pas à ce dernier. La liste exacte des variables à
> miroiter est figée ci-dessous dans la section Phase 1.

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

### ✅ Conclusions elicitation (2026-04-24)

Recherches effectuées via PyPI JSON API + GitHub Releases le 24 avril
2026. Versions figées ci-dessous, pins en mode **caret** (`>=X.Y,<X+1`)
sauf ruff qui vit en `0.y.z` (pin `<0.16`).

| Dep | Version figée | Source | Note |
|---|---|---|---|
| Python | **3.12** | local + Chainlit support | `<3.14,>=3.10`, 3.12.3 déjà installé |
| `anthropic` | `>=0.97.0,<0.98` | [PyPI](https://pypi.org/pypi/anthropic/json) | SDK officiel Anthropic, Messages API + tool_use |
| `mcp` | `>=1.27.0,<2` | PyPI, 2026-04-02 | SDK MCP officiel (client streamable-http) |
| `chainlit` | `>=2.11.1,<3` | PyPI, 2026-04-22 | supporte py 3.10→3.13 |
| `pydantic` | `>=2.13.3,<3` | PyPI, 2026-04-20 | v2 stable |
| `structlog` | `>=25.5.0,<26` | PyPI, 2025-10-27 | logging JSON structuré |
| `python-dotenv` | `>=1.2.2,<2` | PyPI, 2026-03-01 | |
| `tenacity` | `>=9.1.4,<10` | PyPI, 2026-02-07 | retry/backoff (S02, S10) |
| `pytest` | `>=9.0.3,<10` | PyPI | |
| `pytest-asyncio` | `>=1.3.0,<2` | PyPI, 2025-11-10 | **support pytest 9 ajouté en 1.3.0** — ne pas prendre 1.1.x |
| `pytest-cov` | `>=7.1.0,<8` | PyPI, 2026-03-21 | |
| `ruff` | `>=0.15.11,<0.16` | PyPI | |
| `pre-commit` | `>=4.6.0,<5` | PyPI | |
| `hatchling` (build) | `>=1.29.0,<2` | PyPI, 2026-02-23 | backend PEP 517 |

Pré-commit pinné :

- `astral-sh/ruff-pre-commit@v0.15.11`
- `gitleaks/gitleaks@v8.30.1` (2026-03-21)
- `pre-commit/pre-commit-hooks@v5.0.0` pour les hooks basiques
  (`trailing-whitespace`, `end-of-file-fixer`, `check-yaml`,
  `check-toml`, `check-merge-conflict`, `check-added-large-files`).

GitHub Actions pinnés :

- `actions/checkout@v4`
- `astral-sh/setup-uv@v8` (dernière release `v8.1.0`, 2026-04-16).
  Pour un exo week-end on pin au tag flottant `@v8` ; passer à un
  pin SHA (`08807647e7069bb48b6ef5acd8ec9567f424441b`) est documenté
  en "next step" dans le README.

### 🧠 Choix de SDK — clarification vs cahier §5.1

Le cahier nomme "Claude Agent SDK (Python)". En 2026, **deux** noms
cohabitent :

1. Le package PyPI `claude-agent-sdk` → c'est un **wrapper du CLI
   Claude Code**, pensé pour automatiser l'outil CLI. Pas pour
   construire une app web agent avec tool_use. **On l'exclut.**
2. Le SDK `anthropic` + le SDK `mcp` combinés → c'est le **chemin
   officiel** pour une app agent : Messages API avec `tools=[...]` +
   client MCP streamable-http qui expose les tools Pappers au LLM.
   C'est ce qu'on retient.

Conséquence : **le cahier §5.1 est à amender** en S09 (polish) pour
remplacer "Claude Agent SDK" par "Anthropic SDK + MCP SDK". Pour S01,
on pose les deux deps `anthropic` + `mcp` et c'est tout.

### 📋 Variables `.env.example` (miroir du `.env` local validé)

Exactement les noms présents dans `.env` (vérifiés le 2026-04-24) —
valeurs vides pour les secrets, valeurs constantes pour les IDs voix
et flags :

```bash
# ─── Anthropic ────────────────────────────────────────────────
ANTHROPIC_API_KEY=

# ─── Pappers MCP ──────────────────────────────────────────────
# URL complète construite côté serveur : https://mcp.pappers.fr/${PAPPERS_API_KEY}
PAPPERS_API_KEY=

# ─── ElevenLabs (stretch S10) ─────────────────────────────────
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_GAELLE=tKaoyJLW05zqV0tIH9FD
ELEVENLABS_VOICE_GUILLAUME=ohItIVrXTBI80RrUECOD
ELEVENLABS_MODEL_ID=eleven_multilingual_v2

# ─── Feature flags ────────────────────────────────────────────
ENABLE_VOICE_BRIEF=false

# ─── Observabilité ────────────────────────────────────────────
LOG_LEVEL=INFO
```

> Les 3 IDs de voix ElevenLabs et `eleven_multilingual_v2` sont des
> **constantes publiques** (cahier §19.5) — OK de les committer.

### 📌 Points résolus

- [x] **Python 3.12** (pas 3.13) : 3.12.3 déjà installé localement,
      toutes les deps compatibles, image Docker officielle stable.
      Figé dans `.python-version` → `3.12`.
- [x] **Pas de `mypy`** : ruff + Pydantic v2 runtime suffisent pour
      un exo week-end. Ajout documenté en "next step" README.
- [x] **Layout `src/`** : retenu (standard PEP 621 moderne, évite les
      collisions d'import entre package et tests).
- [x] **`uv.lock` committé** : requis pour `uv sync --frozen` en CI
      et dans l'image Docker. Dev agent : lancer `uv sync --extra dev`
      en phase 2 puis `git add uv.lock`.
- [x] **Dev deps via `[project.optional-dependencies]`** (pas
      `[dependency-groups]` PEP-735) : l'ensemble des Makefiles et
      commandes utilisent déjà `uv sync --extra dev`, on reste
      cohérent. PEP-735 est un "next step" sans impact fonctionnel.
- [x] **Ruff règles** : `E, F, I, B, SIM, UP, S` (style + bugbear +
      simplify + pyupgrade + bandit). `S101` ignoré (assert OK dans
      tests). `S` ignoré entièrement dans `tests/**`.
- [x] **Base Docker** : `python:3.12-slim-bookworm` — stable, pas
      `slim-trixie` qui est encore trop frais en avril 2026.

### 🔬 Ordre de vérification final (phase 1 → phase 2)

Le dev agent (phase 2) doit reproduire cette séquence avant commit :

```bash
uv sync --extra dev                # génère .venv + uv.lock
uv run pre-commit install          # installe les git hooks
uv run ruff check src tests        # doit être vert
uv run ruff format --check src tests
uv run pytest tests/unit -v        # 1 test smoke doit passer
uv run pre-commit run --all-files  # lint + gitleaks
docker build -t genial-agent:local .
git add uv.lock                    # ne pas oublier
```

### Commit phase 1

`story(S01): refine — python 3.12, deps pinned april 2026, SDK clarified`

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
requires-python = ">=3.12,<3.13"
readme = "README.md"
license = { text = "MIT" }
authors = [{ name = "Lancelot Oudin" }]
dependencies = [
    "anthropic>=0.97.0,<0.98",
    "mcp>=1.27.0,<2",
    "chainlit>=2.11.1,<3",
    "pydantic>=2.13.3,<3",
    "structlog>=25.5.0,<26",
    "python-dotenv>=1.2.2,<2",
    "tenacity>=9.1.4,<10",
]

[project.optional-dependencies]
dev = [
    "pytest>=9.0.3,<10",
    "pytest-asyncio>=1.3.0,<2",
    "pytest-cov>=7.1.0,<8",
    "ruff>=0.15.11,<0.16",
    "pre-commit>=4.6.0,<5",
]

[build-system]
requires = ["hatchling>=1.29.0,<2"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/genial_agent"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra --strict-markers --strict-config"
markers = [
    "integration: requires real API keys (skipped when absent)",
]

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = [".venv", ".chainlit", "dist", "build"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "SIM", "UP", "S"]
ignore = [
    "S101",  # assert OK (tests + pré-conditions internes)
    "E501",  # ligne longue — ruff format gère
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S"]  # pas de check sécurité dans les tests
```

> **Note build backend** : `hatchling` avec `[tool.hatch.build.targets.wheel]
> packages = ["src/genial_agent"]` est nécessaire pour que hatchling
> trouve le package sous `src/` (sinon il cherche à la racine).

#### `.gitignore`

**Déjà présent à la racine** (commit `a35a24e`) — couvre `.env*`,
`__pycache__/`, `.venv/`, `*.egg-info/`, `.ruff_cache/`,
`.pytest_cache/`, `.mypy_cache/`, `.coverage`, `htmlcov/`,
`.chainlit/`, `.files/`, `.DS_Store`, `.idea/`, `.vscode/`, `*.swp`,
`*.log`, `*.local`.

Le dev agent **ne le réécrit pas**. Il peut ajouter une ligne si un
nouveau cache non couvert apparaît (ex : `.hatch/`, `node_modules/`
si chainlit génère du front buildé).

**Important** : `uv.lock` **ne doit pas** être dans `.gitignore` — il
est committé pour reproductibilité CI/Docker.

#### `.pre-commit-config.yaml`

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-merge-conflict
      - id: check-added-large-files
        args: ["--maxkb=500"]
      - id: detect-private-key

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.11
    hooks:
      - id: ruff
        args: ["--fix"]
      - id: ruff-format

  - repo: https://github.com/gitleaks/gitleaks
    rev: v8.30.1
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
```

> **Prérequis image** : `uv.lock` doit exister à la racine. Il est
> généré par `uv sync --extra dev` localement et **committé**. Si
> `uv.lock` manque, `uv sync --frozen` échoue — c'est voulu (garde-fou
> reproductibilité).

#### `.github/workflows/ci.yml`

```yaml
name: CI
on:
  push:
    branches: ["**"]
  pull_request:

permissions:
  contents: read

jobs:
  test:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4

      - name: Set up uv
        uses: astral-sh/setup-uv@v8
        with:
          python-version: "3.12"
          enable-cache: true

      - name: Install deps (frozen)
        run: uv sync --extra dev --frozen

      - name: Ruff lint
        run: uv run ruff check src tests

      - name: Ruff format check
        run: uv run ruff format --check src tests

      - name: Unit tests
        run: uv run pytest tests/unit -v

      - name: Gitleaks scan
        uses: gitleaks/gitleaks-action@v2
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

> **Choix `--frozen`** : la CI échoue si `uv.lock` diverge de
> `pyproject.toml`. Ça force les devs à regenerer le lock en local.
>
> **Gitleaks action** : `gitleaks/gitleaks-action@v2` est l'officielle
> (`zricethezav/gitleaks-action` cité dans la version brute était
> l'ancien repo — redirige vers le nouveau depuis 2024).

### Tests à produire

#### Unitaires

```python
# tests/unit/test_S01_scaffold.py
"""Smoke tests du scaffold S01. Vérifie que le packaging est correct."""
from __future__ import annotations


def test_package_importable() -> None:
    import genial_agent

    assert genial_agent.__name__ == "genial_agent"


def test_package_has_version() -> None:
    """La version doit être exposée depuis __init__.py pour le /health (S07)."""
    from genial_agent import __version__

    assert isinstance(__version__, str)
    assert __version__  # non vide
```

Le fichier `src/genial_agent/__init__.py` expose donc :

```python
"""Agent IA spécialisé entreprises FR via MCP Pappers."""
from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
```

Un seul test smoke suffit pour cette story ; on ajoute un check
`__version__` parce que S07 (`/health`) en aura besoin et autant
commettre la contrainte dès maintenant.

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

### Check-list spécifique — ✅ vérifiée 2026-04-24

- [x] `pyproject.toml` : toutes les deps sont en **caret-range**
      (`>=X.Y,<X+1`) — pas de `*` nu, pas de `>=X` sans borne haute.
      `uv.lock` présent et committé fige les versions exactes pour
      CI/Docker (2210 lignes, anthropic 0.97.0 / mcp 1.27.0 /
      chainlit 2.11.1 / pytest-asyncio 1.3.0).
- [x] `.gitignore` couvre bien `.env`, `.venv`, `.chainlit`. `uv.lock`
      **n'y est pas** (confirmé `git ls-files | grep uv.lock` → tracké).
- [x] `.env.example` n'a aucune valeur secrète (pour les voice IDs
      ElevenLabs et `ELEVENLABS_MODEL_ID` c'est OK, ce sont des
      constantes publiques documentées au cahier §19.5). Allowlist
      dédiée dans `.gitleaks.toml` — bonus, scope limité à `.env.example`.
- [x] `Dockerfile` utilise un user non-root (`USER agent`) et l'image
      buildée localement (`docker build -t genial-agent:local .`)
      retourne 0. Vérifié `docker run` → `uid=1000(agent)`.
- [ ] CI GitHub Actions s'exécute et passe (non vérifié en local —
      YAML inspecté, cohérent avec la spec, à confirmer au push).
- [x] `make install` fonctionne depuis un clone frais
      (`.venv` généré, `uv.lock` respecté).
- [x] `gitleaks detect --source .` retourne 0 issue sur l'historique
      git. Le `.env` local non-tracké contient évidemment les vraies
      clés, mais il est correctement gitignoré (`git check-ignore`
      ok) et jamais committé (`git log --all --full-history -- .env`
      vide).
- [x] `pre-commit run --all-files` retourne 0 (10 hooks verts dont
      gitleaks + ruff + ruff-format).
- [x] Pas de TODO / FIXME oublié dans les fichiers scaffoldés.
- [x] Cahier §5.1 : note ajoutée dans le README (lignes 21-24)
      pointant vers la clarification `anthropic` + `mcp` vs package
      PyPI `claude-agent-sdk`.

### Observations non-bloquantes (améliorations futures)

1. **`make test` masque les échecs d'intégration** — la cible chaîne
   unit + integration avec `|| true` sur l'intégration. Cohérent avec
   la spec (pas de test d'intégration en S01), mais à durcir quand
   S02 ajoutera les vrais tests MCP pour éviter les faux positifs
   vert/rouge.
2. **`astral-sh/setup-uv@v8` non-pinné SHA** — déjà documenté comme
   "next step" dans le README et accepté pour un exo week-end.
3. **Divergence `uv` version** — Dockerfile builder utilise
   `ghcr.io/astral-sh/uv:0.11.7`, local en `0.10.6`. Pas de conflit
   fonctionnel (lock respecté), mais un `uv sync --frozen` produit
   le même `.venv` quelle que soit la version uv.
4. **`.gitleaks.toml` hors spec** — introduit par le dev agent pour
   allowlister les voice IDs ElevenLabs dans `.env.example`.
   Justifié (cahier §19.5 les qualifie de constantes publiques),
   scope limité à ce seul fichier, `useDefault = true` conserve
   toutes les règles standards.

### Commit phase 3

`review(S01): approved`

---

## ✅ Critères d'acceptation

- [x] `make install` retourne 0.
- [x] `make lint` retourne 0 (ruff check + format check verts,
      5 fichiers formatés).
- [x] `make test` retourne 0 avec 2 tests passant
      (`test_package_importable`, `test_package_has_version`).
- [x] `make docker-build` retourne 0 (image `genial-agent:local`,
      stage final non-root uid 1000).
- [x] `pre-commit run --all-files` retourne 0.
- [ ] CI GitHub est verte sur le commit de phase 2 (à vérifier
      après push — YAML inspecté et cohérent).
- [x] `gitleaks detect` sur l'historique git ne trouve rien.

---

## 📦 Done when

- [x] Phase 1 commitée (`story(S01): refine`) — commit `4e73789`.
- [x] Phase 2 commitée (`feat(S01): scaffold`) — commit `cc562a1`,
      tests verts.
- [x] Phase 3 approuvée (`review(S01): approved`).
- [x] Ligne S01 mise à jour dans `docs/stories/README.md` → ✅.
- [ ] Push sur `claude/builder-evaluation-exercise-34Iyu`
      (à faire manuellement après le commit review).
