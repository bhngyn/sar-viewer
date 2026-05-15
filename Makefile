.PHONY: help up down logs build ci lint type test fmt precommit clean

help:
	@echo "Targets:"
	@echo "  up         - docker compose up -d --build"
	@echo "  down       - docker compose down"
	@echo "  logs       - tail logs from all services"
	@echo "  build      - docker compose build (no start)"
	@echo "  lint       - ruff check"
	@echo "  fmt        - ruff format"
	@echo "  type       - mypy strict on shared/ and services/api/src"
	@echo "  test       - pytest"
	@echo "  ci         - lint + type + test + docker compose build"
	@echo "  precommit  - run pre-commit on all files"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=200

build:
	docker compose build

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

type:
	uv run mypy shared services/api/src services/processor/src

test:
	uv run pytest

ci: lint type test build

precommit:
	uv run pre-commit run --all-files

clean:
	docker compose down -v --remove-orphans
	rm -rf .pytest_cache .ruff_cache .mypy_cache
