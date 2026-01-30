.PHONY: help install dev test lint format typecheck run-api run-dashboard run-collector \
	db-up db-down db-logs db-reset db-migrate db-upgrade db-downgrade db-setup clean demo lock audit

# Default target
help:
	@echo "Social Attention Monitor (SAM) - Available Commands"
	@echo ""
	@echo "  install       Install production dependencies"
	@echo "  dev           Install development dependencies"
	@echo "  test          Run tests with coverage"
	@echo "  lint          Run linter (ruff)"
	@echo "  format        Format code (ruff)"
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

# Installation
install:
	pip install -e .

dev:
	pip install -e ".[dev]"
	pre-commit install

# Testing
test:
	pytest tests/ -v --cov=sam --cov-report=term-missing

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
	mypy src/

# Running services
run-api:
	uvicorn sam.api.main:app --reload --host 0.0.0.0 --port 8000

run-dashboard:
	streamlit run src/dashboard/app.py --server.port 8501

run-collector:
	python -m sam.scheduler.runner

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

audit:
	pip-audit
