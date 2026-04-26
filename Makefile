.PHONY: install run test test-unit test-integration test-all lint format docker-build precommit prewarm-comptes

install:
	uv sync --extra dev
	uv run pre-commit install

run:
	uv run chainlit run src/genial_agent/app.py -w

# Default `make test` = unit seulement. Rapide, gratuit, lancé par les
# Dev Agents et Review Agents de chaque story. Voir `test-integration`
# et `test-all` pour les live tests (opt-in).
test:
	uv run pytest tests/unit -v

test-unit:
	uv run pytest tests/unit -v

# Live tests — consomme des crédits Anthropic + Pappers (~2-5 min, ~12
# crédits Pappers par run S03). À lancer explicitement :
#   - S09 pre-démo ;
#   - debug d'une régression détectée par un unit test insuffisant ;
#   - smoke test hebdo.
# Le ``-m integration`` override le ``-m 'not integration'`` posé par
# défaut dans pyproject.toml.
test-integration:
	uv run pytest tests/integration -v -m integration

# Convenience: tout jouer (unit + integration). Pré-démo uniquement.
test-all:
	uv run pytest tests/unit -v
	uv run pytest tests/integration -v -m integration

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

# S09.6 (E1) — Pre-warm le cache MCP pour `comptes-entreprise` sur les
# 4 entités golden × 3 années. À lancer manuellement au refill du pack
# mensuel Pappers (le 30/04 puis mensuel) — le tool refuse les jetons
# PAYG (bug serveur). Coût attendu : ~24 crédits abo. Output dans
# `data/mcp_cache.json` (versionné). Commit le diff après run pour que
# le cache enrichi soit propagé à Railway au prochain build.
prewarm-comptes:
	MCP_CACHE_PERSIST_PATH=data/mcp_cache.json \
		uv run python scripts/prewarm_comptes_entreprise.py
