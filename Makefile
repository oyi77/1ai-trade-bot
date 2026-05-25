.PHONY: test lint install clean format typecheck test-unit test-integration help

PYTHON ?= python3
PIP    ?= pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install project + dev dependencies
	$(PIP) install -r requirements.txt
	$(PIP) install -e ".[dev]" 2>/dev/null || $(PIP) install -e .

install-all: ## Install with all optional deps (dex, tui, dev)
	$(PIP) install -e ".[all]"

test: ## Run all tests with pytest
	$(PYTHON) -m pytest tests/ -v --tb=short

test-unit: ## Run unit tests only
	$(PYTHON) -m pytest tests/unit/ -v --tb=short -m "not integration"

test-integration: ## Run integration tests only
	$(PYTHON) -m pytest tests/integration/ -v --tb=short -m integration

test-cov: ## Run tests with coverage report
	$(PYTHON) -m pytest tests/ -v --tb=short --cov=trading_bot --cov-report=term-missing

lint: ## Run ruff linter
	$(PYTHON) -m ruff check trading_bot/ tests/

format: ## Auto-format code with ruff
	$(PYTHON) -m ruff format trading_bot/ tests/
	$(PYTHON) -m ruff check --fix trading_bot/ tests/

typecheck: ## Run mypy type checker
	$(PYTHON) -m mypy trading_bot/

clean: ## Remove __pycache__, .pyc, build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ htmlcov/ .coverage

check: lint test ## Run lint + test (CI shortcut)
