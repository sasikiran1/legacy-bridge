# Everything runs in containers, so the only host requirements are Docker and make.
# On Windows, run make from WSL (Ubuntu): `sudo apt install make` once.

SHELL := /bin/bash
COMPOSE := docker compose
TESTBED := $(COMPOSE) --profile test run --rm testbed
SCENARIOS ?= all
SCENARIO ?= healthy
TASKS ?=
RECORDED_SCENARIOS = $(sort $(shell sed -n 's/^ *"\([a-z_]*\)\/.*/\1/p' cassettes/index.json 2>/dev/null))

MAVEN_IMAGE := maven:3.9.16-eclipse-temurin-25-noble
NODE_IMAGE := node:26.9.0

.DEFAULT_GOAL := help
.PHONY: help up down reset-db logs seed lint typecheck test portal-test web-build check probe oracles demo \
	proxy-up leak proxy-demo bench-pii bench-latency bench-tokens audit-verify agent-stack record replay-check \
	demo-fallback traces

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

test: .env ## Python unit and property tests (includes the seed-drift check; no models)
	$(TESTBED) uv run pytest

leak: .env ## Canary leak gate through the proxy with the production detector (loads models)
	$(TESTBED) uv run pytest -m models tests/leak

portal-test: ## Portal build and JUnit/Testcontainers tests
	docker run --rm -v "$(CURDIR)/portal:/src" -v legacy-bridge-m2:/root/.m2 \
		-v /var/run/docker.sock:/var/run/docker.sock --add-host=host.docker.internal:host-gateway \
		-e TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal -w /src $(MAVEN_IMAGE) mvn -B -ntp verify

web-build: ## Type-check and build the SPA
	docker run --rm -v "$(CURDIR)/portal/web:/src" -v /src/node_modules -w /src $(NODE_IMAGE) \
		sh -c "npm ci --no-audit --no-fund && npm run build"

check: lint typecheck test leak portal-test web-build ## Everything CI runs except the oracle suite

probe: up ## Print the public-API outcome matrix across chaos scenarios
	$(TESTBED) uv run python -m testbed.probe

oracles: up ## Scripted Playwright oracles (SCENARIOS=healthy,dom_drift to narrow)
	$(TESTBED) uv run pytest -m oracle --scenarios $(SCENARIOS)

demo: up ## M1 demo: API matrix, then UI oracles under healthy, DOM drift, poor a11y and CAPTCHA
	$(TESTBED) uv run python -m testbed.probe
	$(TESTBED) uv run pytest -m oracle --scenarios healthy,dom_drift,poor_a11y,captcha -q

proxy-up: .env ## Start Redis and the PII proxy on http://localhost:8090 (first start downloads ~1.2 GB of models)
	$(COMPOSE) up -d --build redis proxy

proxy-demo: .env ## M2 demo: mask, stream-rehydrate, tool-boundary policy, audit chain and tamper check
	$(COMPOSE) up -d redis
	$(TESTBED) uv run python -W ignore -m proxy.demo

bench-pii: .env ## PII detection P/R/F1 per entity type and detector config -> analysis/results/
	$(TESTBED) uv run python -W ignore -m analysis.pii_benchmark

bench-latency: .env ## Latency the proxy adds (CPU detection, Redis vault) -> analysis/results/
	$(TESTBED) uv run python -W ignore -m analysis.proxy_latency

bench-tokens: .env ## LLM-token overhead of token renderings -> analysis/results/
	$(TESTBED) uv run python -W ignore -m analysis.token_overhead

audit-verify: ## Verify the proxy's audit chain
	$(TESTBED) uv run python -m proxy.audit verify .cache/audit/proxy.jsonl

# The proxy reads LLM_MODE at start, so each agent target recreates it in the mode it needs.
agent-stack:
	LLM_MODE=$(LLM_MODE) $(COMPOSE) up -d --build --wait postgres portal redis
	LLM_MODE=$(LLM_MODE) $(COMPOSE) up -d --force-recreate --wait proxy

record: LLM_MODE := record
record: agent-stack ## Record cassettes with the local model (SCENARIO=healthy TASKS=claim_status-01,... or TASKS=all)
	$(TESTBED) uv run python -W ignore -m agent.runner --scenario $(SCENARIO) \
		$(if $(filter all,$(TASKS)),--all,--tasks $(TASKS)) --record --label record; \
	status=$$?; LLM_MODE=replay $(COMPOSE) up -d --force-recreate --wait proxy; exit $$status

replay-check: LLM_MODE := replay
replay-check: agent-stack ## Replay every recorded task and fail if any outcome differs from its recording (no model)
	for s in $(RECORDED_SCENARIOS); do \
		$(TESTBED) uv run python -W ignore -m agent.runner --scenario $$s --check-replay --label replay || exit 1; \
	done

demo-fallback: LLM_MODE := replay
demo-fallback: agent-stack ## M3 demo: removed and retired endpoints push the agent to the browser (replayed, no model)
	$(TESTBED) uv run python -W ignore -m agent.runner --scenario api_degraded --check-replay --label demo

traces: LLM_MODE := replay
traces: agent-stack ## Replay recorded healthy tasks with tracing to Phoenix (http://localhost:6006), then check spans for canaries
	$(COMPOSE) up -d phoenix
	$(TESTBED) sh -c "OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006 uv run python -W ignore -m agent.runner \
		--scenario healthy --check-replay --label traces && uv run pytest -m phoenix tests/leak/test_traces.py"
