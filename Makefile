# Everything runs in containers, so the only host requirements are Docker and make.
# On Windows, run make from WSL (Ubuntu): `sudo apt install make` once.

SHELL := /bin/bash
COMPOSE := docker compose
TESTBED := $(COMPOSE) --profile test run --rm testbed
SCENARIOS ?= all

MAVEN_IMAGE := maven:3.9.16-eclipse-temurin-25-noble
NODE_IMAGE := node:26.9.0

.DEFAULT_GOAL := help
.PHONY: help up down reset-db logs seed lint typecheck test portal-test web-build check probe oracles demo

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  %-12s %s\n", $$1, $$2}'

.env:
	@echo "Create .env from .env.example first (see README)." >&2; exit 1

up: .env ## Build and start Postgres and the portal on http://localhost:8080
	$(COMPOSE) up -d --build postgres portal

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

reset-db: ## Stop the stack and delete the database volume
	$(COMPOSE) down -v

logs: ## Follow portal logs
	$(COMPOSE) logs -f portal

seed: .env ## Regenerate the committed seed SQL, tasks.json and canaries.json
	$(TESTBED) uv run python -m testbed.seed write

lint: .env ## ruff lint and format check
	$(TESTBED) sh -c "uv run ruff check . && uv run ruff format --check ."

typecheck: .env ## mypy (strict)
	$(TESTBED) uv run mypy

test: .env ## Python unit and property tests (includes the seed-drift check)
	$(TESTBED) uv run pytest

portal-test: ## Portal build and JUnit/Testcontainers tests
	docker run --rm -v "$(CURDIR)/portal:/src" -v legacy-bridge-m2:/root/.m2 \
		-v /var/run/docker.sock:/var/run/docker.sock --add-host=host.docker.internal:host-gateway \
		-e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal -w /src $(MAVEN_IMAGE) mvn -B -ntp verify

web-build: ## Type-check and build the SPA
	docker run --rm -v "$(CURDIR)/portal/web:/src" -v /src/node_modules -w /src $(NODE_IMAGE) \
		sh -c "npm ci --no-audit --no-fund && npm run build"

check: lint typecheck test portal-test web-build ## Everything CI runs except the oracle suite

probe: up ## Print the public-API outcome matrix across chaos scenarios
	$(TESTBED) uv run python -m testbed.probe

oracles: up ## Scripted Playwright oracles (SCENARIOS=healthy,dom_drift to narrow)
	$(TESTBED) uv run pytest -m oracle --scenarios $(SCENARIOS)

demo: up ## M1 demo: API matrix, then UI oracles under healthy, DOM drift, poor a11y and CAPTCHA
	$(TESTBED) uv run python -m testbed.probe
	$(TESTBED) uv run pytest -m oracle --scenarios healthy,dom_drift,poor_a11y,captcha -q
