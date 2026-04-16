PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

COLLECTION_NAMESPACE := devitops
COLLECTION_NAME      := read_config
COLLECTION_VERSION   := $(shell awk '/^version:/ {print $$2}' galaxy.yml)
COLLECTION_TARBALL   := $(COLLECTION_NAMESPACE)-$(COLLECTION_NAME)-$(COLLECTION_VERSION).tar.gz
COLLECTIONS_PATH     ?= $(HOME)/.ansible/collections

.PHONY: help venv install test unit integration test-all coverage coverage-html \
        build install-local uninstall-local clean

help:
	@echo "Testing:"
	@echo "  venv             Create virtualenv at $(VENV) (uses uv if available, else venv)"
	@echo "  install          Install dev dependencies into $(VENV)"
	@echo "  test             Alias for 'unit' (fast default)"
	@echo "  unit             Run unit tests only"
	@echo "  integration      Run integration tests only (subprocess module invocation)"
	@echo "  test-all         Run unit + integration tests"
	@echo "  coverage         Run unit tests with terminal coverage report"
	@echo "  coverage-html    Run unit tests with HTML coverage report at htmlcov/"
	@echo ""
	@echo "Collection packaging:"
	@echo "  build            ansible-galaxy collection build → $(COLLECTION_TARBALL)"
	@echo "  install-local    Install the built tarball into \$$COLLECTIONS_PATH"
	@echo "                   (default: $(COLLECTIONS_PATH))"
	@echo "  uninstall-local  Remove the installed collection from \$$COLLECTIONS_PATH"
	@echo ""
	@echo "  clean            Remove caches, coverage, and build artifacts"

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

build: install
	$(BIN)/ansible-galaxy collection build --force
	@echo "Built $(COLLECTION_TARBALL)"

install-local: build
	$(BIN)/ansible-galaxy collection install --force \
		-p $(COLLECTIONS_PATH) $(COLLECTION_TARBALL)
	@echo "Installed to $(COLLECTIONS_PATH)/ansible_collections/$(COLLECTION_NAMESPACE)/$(COLLECTION_NAME)"

uninstall-local:
	rm -rf $(COLLECTIONS_PATH)/ansible_collections/$(COLLECTION_NAMESPACE)/$(COLLECTION_NAME)

clean:
	rm -rf .pytest_cache htmlcov .coverage .coverage.*
	rm -f $(COLLECTION_NAMESPACE)-$(COLLECTION_NAME)-*.tar.gz
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
