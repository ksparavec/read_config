PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

.PHONY: help venv install test unit integration test-all coverage coverage-html clean

help:
	@echo "Targets:"
	@echo "  venv          Create virtualenv at $(VENV) (uses uv if available, else venv)"
	@echo "  install       Install dev dependencies into $(VENV)"
	@echo "  test          Alias for 'unit' (fast default)"
	@echo "  unit          Run unit tests only"
	@echo "  integration   Run integration tests only (subprocess module invocation)"
	@echo "  test-all      Run unit + integration tests"
	@echo "  coverage      Run unit tests with terminal coverage report"
	@echo "  coverage-html Run unit tests with HTML coverage report at htmlcov/"
	@echo "  clean         Remove caches and coverage artifacts"

venv:
	@if [ ! -x "$(BIN)/python" ]; then \
		if command -v uv >/dev/null 2>&1; then \
			uv venv --seed $(VENV); \
		else \
			$(PYTHON) -m venv $(VENV); \
		fi; \
	fi

install: venv
	$(BIN)/python -m pip install -q --upgrade pip
	$(BIN)/python -m pip install -q -r requirements-dev.txt

test: unit

unit: install
	$(BIN)/pytest tests/unit

integration: install
	$(BIN)/pytest tests/integration

test-all: install
	$(BIN)/pytest tests/unit tests/integration

coverage: install
	$(BIN)/pytest tests/unit --cov --cov-report=term-missing

coverage-html: install
	$(BIN)/pytest tests/unit --cov --cov-report=html
	@echo "HTML report: htmlcov/index.html"

clean:
	rm -rf .pytest_cache htmlcov .coverage .coverage.*
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
