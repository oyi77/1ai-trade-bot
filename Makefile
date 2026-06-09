.PHONY: install test test-cov lint format typecheck clean all docker-build docker-up

# ── Installation ──────────────────────────────────────────────────────────────

install:
	pip install -e .[dev]

# ── Testing ───────────────────────────────────────────────────────────────────

test:
	pytest tests/ -v

test-cov:
	pytest tests/ --cov=tradebot --cov-report=term-missing

# ── Linting & Formatting ──────────────────────────────────────────────────────

lint:
	ruff check tradebot/ tests/

format:
	ruff format tradebot/ tests/

typecheck:
	mypy tradebot/

# ── Housekeeping ──────────────────────────────────────────────────────────────

clean:
	rm -rf __pycache__ *.pyc .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete

# ── Docker ────────────────────────────────────────────────────────────────────

docker-build:
	docker compose build

docker-up:
	docker compose up -d

# ── Pipeline ──────────────────────────────────────────────────────────────────

all: lint typecheck test docker-build
