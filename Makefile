PYTHON ?= python3
VENV   ?= .venv
BIN    := $(VENV)/bin

COLLECTION_NAMESPACE := devitops
COLLECTION_NAME      := ansible
COLLECTION_VERSION   := $(shell awk '/^version:/ {print $$2}' galaxy.yml)
COLLECTION_TARBALL   := $(COLLECTION_NAMESPACE)-$(COLLECTION_NAME)-$(COLLECTION_VERSION).tar.gz
COLLECTIONS_PATH     ?= $(HOME)/.ansible/collections

LIVE_ENV := PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
LIVE_SH  := tests/live/containers.sh

GALAXY_SERVER        ?= https://galaxy.ansible.com

.PHONY: help venv install test unit integration test-all coverage coverage-html \
        live live-install live-up live-down live-reset live-status live-logs \
        live-errors \
        build install-local uninstall-local publish clean

help:
	@echo "Testing:"
	@echo "  venv             Create virtualenv at $(VENV) (uses uv if available, else venv)"
	@echo "  install          Install dev dependencies into $(VENV)"
	@echo "  test             Alias for 'unit' (fast default)"
	@echo "  unit             Run unit tests only"
	@echo "  integration      Run integration tests only (subprocess module invocation)"
	@echo "  test-all         Run unit + integration + live tests"
	@echo "  coverage         Run unit tests with terminal coverage report"
	@echo "  coverage-html    Run unit tests with HTML coverage report at htmlcov/"
	@echo ""
	@echo "Live backend tests (real services in Podman containers):"
	@echo "  live-install     Install the live-suite client libraries"
	@echo "  live-up          Start every backend container (parallel) and wait"
	@echo "  live             Run tests/live against the running containers"
	@echo "  live-status      Show each container's state and readiness"
	@echo "  live-logs        Dump all container logs (LIVE_SVC=<name> for one)"
	@echo "  live-errors      Show error lines found in container logs"
	@echo "  live-down        Stop and remove every backend container (DB kept)"
	@echo "  live-reset       Same, and wipe the persistent PostgreSQL volume"
	@echo ""
	@echo "Collection packaging:"
	@echo "  build            ansible-galaxy collection build → $(COLLECTION_TARBALL)"
	@echo "  install-local    Install the built tarball into \$$COLLECTIONS_PATH"
	@echo "                   (default: $(COLLECTIONS_PATH))"
	@echo "  uninstall-local  Remove the installed collection from \$$COLLECTIONS_PATH"
	@echo "  publish          ansible-galaxy collection publish to \$$GALAXY_SERVER"
	@echo "                   (default: $(GALAXY_SERVER))"
	@echo "                   Requires GALAXY_API_TOKEN in the environment."
	@echo ""
	@echo "  clean            Remove $(VENV), test/lint/type caches, coverage,"
	@echo "                   and build artifacts"

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

# Everything, live suite included. The live tests skip if the containers are
# not running, so this stays usable without Podman -- but it says so loudly
# rather than reporting a green run that quietly tested six fewer backends.
test-all: install live-install
	@if ! $(LIVE_SH) status 2>/dev/null | awk 'NR>1 && $$4!="yes"{bad=1} END{exit bad}'; then \
		echo "=============================================================="; \
		echo " NOTE: live backend services are not all ready."; \
		echo "       tests/live will SKIP. Run 'make live-up' to include it,"; \
		echo "       or set RC_LIVE_REQUIRE=1 to make the skip a failure."; \
		echo "=============================================================="; \
	fi
	$(LIVE_ENV) $(BIN)/pytest tests/unit tests/integration tests/live

coverage: install
	$(BIN)/pytest tests/unit --cov --cov-report=term-missing

coverage-html: install
	$(BIN)/pytest tests/unit --cov --cov-report=html
	@echo "HTML report: htmlcov/index.html"

# --- live backend tests ----------------------------------------------------
#
# Containers are started once and reused: `make live` never starts or stops
# anything, so repeated runs pay no container startup cost. Bring them up with
# `make live-up` and tear them down with `make live-down`.
#
# PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python is required because etcd3's
# protobuf stubs predate protobuf 4.

live-install: install
	$(BIN)/python -m pip install -q -r requirements-live.txt

live-up:
	$(LIVE_SH) up

live-down:
	$(LIVE_SH) down

live-reset:
	$(LIVE_SH) reset

live-status:
	@$(LIVE_SH) status

live-logs:
	@$(LIVE_SH) logs $(LIVE_SVC)

live-errors:
	@$(LIVE_SH) errors

live: live-install
	$(LIVE_ENV) $(BIN)/pytest tests/live

build: install
	$(BIN)/ansible-galaxy collection build --force
	@echo "Built $(COLLECTION_TARBALL)"

install-local: build
	$(BIN)/ansible-galaxy collection install --force \
		-p $(COLLECTIONS_PATH) $(COLLECTION_TARBALL)
	@echo "Installed to $(COLLECTIONS_PATH)/ansible_collections/$(COLLECTION_NAMESPACE)/$(COLLECTION_NAME)"

uninstall-local:
	rm -rf $(COLLECTIONS_PATH)/ansible_collections/$(COLLECTION_NAMESPACE)/$(COLLECTION_NAME)

publish: build
	@if [ -z "$$GALAXY_API_TOKEN" ]; then \
		echo "ERROR: GALAXY_API_TOKEN is not set."; \
		echo "Get a token from $(GALAXY_SERVER)/ui/token and export GALAXY_API_TOKEN."; \
		exit 1; \
	fi
	$(BIN)/ansible-galaxy collection publish \
		--server $(GALAXY_SERVER) \
		--token $$GALAXY_API_TOKEN \
		$(COLLECTION_TARBALL)
	@echo "Published $(COLLECTION_TARBALL) to $(GALAXY_SERVER)"

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .ansible \
	       .coverage .coverage.*
	rm -f $(COLLECTION_NAMESPACE)-$(COLLECTION_NAME)-*.tar.gz
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
