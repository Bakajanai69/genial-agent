.PHONY: install run test test-unit test-integration lint format docker-build precommit

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
