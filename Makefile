.PHONY: help install dev ci-deps test test-ci test-integration lint format typecheck run-api run-dashboard run-collector \
	db-up db-down db-logs db-reset db-migrate db-upgrade db-downgrade db-setup clean demo lock audit ci-check

# Default target
help:
	@echo "Social Attention Monitor (SAM) - Available Commands"
	@echo ""
	@echo "  install       Install production dependencies"
	@echo "  dev           Install development dependencies"
	@echo "  ci-deps       Install pinned CI dependencies"
	@echo "  test          Run tests with coverage"
	@echo "  test-ci       Run tests exactly like CI"
	@echo "  test-integration Run tests with Docker Postgres"
	@echo "  lint          Run linter (ruff)"
	@echo "  format        Format code (ruff)"
	@echo "  ci-check      Run lint, format check, mypy, and tests like CI"
	@echo "  run-api       Start FastAPI server"
	@echo "  run-dashboard Start Streamlit dashboard"
	@echo "  run-collector Start data collector"
	@echo "  db-up         Start local Postgres via Docker"
	@echo "  db-down       Stop local Postgres via Docker"
	@echo "  db-logs       Tail local Postgres logs"
	@echo "  db-reset      Reset local Postgres volume (DANGER)"
	@echo "  db-migrate    Generate new migration"
	@echo "  db-upgrade    Apply migrations"
	@echo "  db-setup      Create database and apply migrations"
	@echo "  clean         Remove build artifacts"
	@echo "  lock          Generate pinned requirements lockfile (uv)"
	@echo "  audit         Run dependency vulnerability scan (pip-audit)"
	@echo ""

# Postgres defaults for integration tests
POSTGRES_USER ?= sam
POSTGRES_PASSWORD ?= sam
TEST_DB ?= sam_test
POSTGRES_PORT ?= 5432

# Installation
install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install

ci-deps:
	$(VENV_PY) -m pip install -r requirements-dev.lock
	$(VENV_PY) -m pip install -e .

# Testing
VENV_PY := $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python)

test:
	$(VENV_PY) -m pytest tests/ -v --cov=sam --cov-report=term-missing

test-ci:
	$(VENV_PY) -m pytest tests/ -v --cov=src/sam --cov-report=xml --cov-report=term

test-integration:
	@echo "Starting Postgres for integration tests..."
	POSTGRES_PORT=5433 docker compose up -d postgres
	@echo "Waiting for Postgres to be ready..."
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		docker exec sam-postgres pg_isready -U $(POSTGRES_USER) -d postgres >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	@docker exec sam-postgres sh -c 'psql -U "$(POSTGRES_USER)" -d postgres -c "ALTER USER \"$(POSTGRES_USER)\" CREATEDB" >/dev/null 2>&1 || true'
	@docker exec sam-postgres sh -c 'createdb -U "$(POSTGRES_USER)" "$(TEST_DB)" 2>/dev/null || true'
	SAM_TEST_DATABASE_URL=postgresql+asyncpg://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@127.0.0.1:5433/$(TEST_DB) \
	$(VENV_PY) -m pytest tests/ -v --cov=sam --cov-report=term-missing

test-fast:
	pytest tests/ -v -x --no-cov

# Linting and formatting
lint:
	ruff check src/ tests/

format:
	ruff check --fix src/ tests/
	ruff format src/ tests/

# Type checking
typecheck:
	$(VENV_PY) -m mypy src/sam/ --ignore-missing-imports

ci-check: ci-deps
	ruff check src tests
	ruff format --check src tests
	$(VENV_PY) -m mypy src/sam/ --ignore-missing-imports
	$(MAKE) test-ci

# Running services
run-api:
	$(VENV_PY) -m uvicorn sam.api.main:app --reload --host 0.0.0.0 --port 8000

run-dashboard:
	$(VENV_PY) -m streamlit run src/dashboard/app.py --server.port 8501

run-collector:
	$(VENV_PY) -m sam.scheduler.runner

# Database
db-up:
	docker compose up -d postgres redis

db-down:
	docker compose down

db-logs:
	docker compose logs -f postgres redis

db-reset:
	docker compose down -v
	docker compose up -d postgres redis

db-migrate:
	@read -p "Migration message: " msg; \
	alembic revision --autogenerate -m "$$msg"

db-upgrade:
	alembic upgrade head

db-downgrade:
	alembic downgrade -1

db-setup:
	@echo "Creating database..."
	createdb sam 2>/dev/null || echo "Database already exists"
	@echo "Applying migrations..."
	alembic upgrade head

# Cleanup
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .coverage htmlcov/

# Demo
demo:
	SAM_DEMO_MODE=true python -m sam.cli demo

# Dependency management
lock:
	uv pip compile pyproject.toml -o requirements.lock
	uv pip compile pyproject.toml --all-extras -o requirements-dev.lock

audit:
	pip-audit
