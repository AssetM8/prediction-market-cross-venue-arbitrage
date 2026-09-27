# prediction-market-cross-venue-arbitrage — paper trading only.
# Requires: uv (Python 3.12), Node 22 + npm. Docker targets require Docker Compose v2.

SHELL := /bin/bash
BACKEND := backend
FRONTEND := frontend
UV := uv
RUN := cd $(BACKEND) && $(UV) run

.PHONY: help install dev dev-backend dev-frontend test test-backend test-frontend lint typecheck \
        format demo scan live-scan smoke e2e build docker-up docker-down fixtures screenshots clean reset-db

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install backend (uv, locked) and frontend (npm ci) dependencies
	cd $(BACKEND) && $(UV) sync --frozen --all-extras
	cd $(FRONTEND) && npm ci --no-audit --no-fund

dev: ## Run the API (http://127.0.0.1:8000) and dashboard (http://127.0.0.1:5173) with reload
	$(MAKE) -j2 dev-backend dev-frontend

dev-backend:
	$(RUN) uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd $(FRONTEND) && npm run dev

test: test-backend test-frontend ## Run backend (pytest) and frontend (vitest) test suites

test-backend:
	$(RUN) pytest -q

test-frontend:
	cd $(FRONTEND) && npm test

lint: ## Ruff (lint + format check) and ESLint
	$(RUN) ruff check .
	$(RUN) ruff format --check .
	cd $(FRONTEND) && npm run lint

format: ## Apply Ruff formatting
	$(RUN) ruff format .
	$(RUN) ruff check --fix .

typecheck: ## mypy --strict (backend) and tsc (frontend)
	$(RUN) mypy app tests
	cd $(FRONTEND) && npm run typecheck

demo: ## Deterministic fixture demo: match, detect, paper-execute, report (no network)
	$(RUN) python -m app.cli demo

scan: ## Full scan using DATA_MODE from the environment (fixture by default)
	$(RUN) python -m app.cli scan

live-scan: ## Read-only scan of live public Kalshi/Polymarket data. Never trades.
	$(RUN) python -m app.cli scan --live

smoke: e2e ## Browser smoke test (alias)

e2e: ## Playwright smoke test: dashboard load + paper execution (starts both servers)
	cd $(FRONTEND) && npx playwright test

build: ## Production build of the dashboard
	cd $(FRONTEND) && npm run build

docker-up: ## Build and start the stack (API :8000, dashboard :8080)
	docker compose up -d --build

docker-down: ## Stop the stack
	docker compose down

reset-db: ## Drop and recreate all tables (paper state included)
	$(RUN) python -m app.cli reset-db

fixtures: ## Regenerate the synthetic fixture set
	cd $(BACKEND) && $(UV) run python ../scripts/generate_fixtures.py

screenshots: ## Capture README screenshots from a running dashboard (http://127.0.0.1:5173)
	cd $(FRONTEND) && node scripts/capture-screenshots.mjs

clean: ## Remove build artefacts and local databases
	rm -rf $(FRONTEND)/dist $(FRONTEND)/test-results $(FRONTEND)/playwright-report
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -f $(BACKEND)/*.db
