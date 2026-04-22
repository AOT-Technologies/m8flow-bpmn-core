.DEFAULT_GOAL := help

UV ?= uv
SRC_DIR := src
TEST_DIR := tests
ALEMBIC_DIR := alembic/versions

.PHONY: help sync sync-postgresql lint lint-fix test test-integration build package package-wheel package-sdist

help:
	@echo "Targets:"
	@echo "  make sync             Sync project dependencies"
	@echo "  make sync-postgresql  Sync dependencies with the Postgres extra"
	@echo "  make lint             Run Ruff"
	@echo "  make lint-fix         Run Ruff with --fix"
	@echo "  make test             Run the unit test suite"
	@echo "  make test-integration  Run the Postgres integration test"
	@echo "  make build            Build wheel and sdist artifacts into dist/"
	@echo "  make package          Alias for build"
	@echo "  make package-wheel    Build only the wheel artifact"
	@echo "  make package-sdist    Build only the source distribution"

sync:
	$(UV) sync

sync-postgresql:
	$(UV) sync --extra postgresql

lint:
	$(UV) run ruff check $(SRC_DIR) $(TEST_DIR) $(ALEMBIC_DIR)

lint-fix:
	$(UV) run ruff check --fix $(SRC_DIR) $(TEST_DIR) $(ALEMBIC_DIR)

test:
	$(UV) run pytest

test-integration:
	$(UV) sync --extra postgresql
	$(UV) run pytest integration/test_postgres_integration.py

build:
	$(UV) build

package: build

package-wheel:
	$(UV) build --wheel

package-sdist:
	$(UV) build --sdist
