# brynhild-deno-plugin Makefile
#
# Usage:
#   make test       - Run all tests
#   make test-cov   - Run tests with coverage report
#   make smoke      - Run quick smoke test
#   make lint       - Run ruff linter
#   make typecheck  - Run mypy type checker
#   make all        - Run lint, typecheck, and tests
#   make clean      - Remove build artifacts
#
# Set PYTHON_EXE to override the Python interpreter (defaults to local.venv).

# Default to local.venv if PYTHON_EXE not set
PYTHON_EXE ?= $(CURDIR)/local.venv/bin/python

.PHONY: test test-cov smoke lint typecheck all clean help

# Default target
help:
	@echo "brynhild-deno-plugin Development Commands"
	@echo ""
	@echo "  make test       Run all pytest tests"
	@echo "  make test-cov   Run tests with coverage report"
	@echo "  make smoke      Run quick smoke test (scripts/smoke_test.py)"
	@echo "  make lint       Run ruff linter"
	@echo "  make typecheck  Run mypy type checker"
	@echo "  make all        Run lint, typecheck, and tests"
	@echo "  make clean      Remove build artifacts"
	@echo ""
	@echo "Python:   $(PYTHON_EXE)"
	@echo "Override: PYTHON_EXE=/path/to/python make test"

# Run all tests
test:
	$(PYTHON_EXE) -m pytest tests/ -v

# Run tests with coverage
test-cov:
	$(PYTHON_EXE) -m pytest tests/ -v \
		--cov=brynhild_deno_plugin \
		--cov-report=term-missing \
		--cov-report=html:coverage_html

# Quick smoke test
smoke:
	$(PYTHON_EXE) scripts/smoke_test.py

# Lint with ruff
lint:
	$(PYTHON_EXE) -m ruff check brynhild_deno_plugin/ tests/ scripts/

# Type check with mypy
typecheck:
	$(PYTHON_EXE) -m mypy brynhild_deno_plugin/ --ignore-missing-imports

# Run all checks
all: lint typecheck test

# Clean build artifacts
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf coverage_html/
	rm -rf .coverage
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

