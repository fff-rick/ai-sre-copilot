SHELL := /bin/bash

.DEFAULT_GOAL := help

.PHONY: help bootstrap proto proto-check format lint test test-python test-go test-web test-testbed test-integration test-kind test-stage3-restart test-stage4-postgres test-stage5-kind eval-offline eval-online eval-retrieval acceptance-stage2 acceptance-stage3 acceptance-stage4 acceptance-stage5 build compose-up compose-down compose-config testbed-up testbed-down testbed-smoke testbed-validate clean

help: ## Show available commands
	@awk 'BEGIN {FS = ":.*## "; printf "Usage: make <target>\n\n"} /^[a-zA-Z_-]+:.*?## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

bootstrap: ## Install locked local dependencies
	uv sync --project services/investigation --all-groups
	pnpm --dir web install --frozen-lockfile

proto: ## Regenerate Go and Python gRPC contracts
	./scripts/generate-proto.sh

proto-check: proto ## Fail when committed generated contracts have drifted
	git diff --exit-code -- services/tool-gateway/gen services/investigation/src/ai_sre_investigation/generated

format: ## Format all source code
	cd services/investigation && uv run ruff format .
	cd services/investigation && uv run ruff check --fix .
	@if command -v go >/dev/null 2>&1; then cd services/tool-gateway && gofmt -w .; else echo "go not installed; skipped gofmt"; fi
	@if command -v go >/dev/null 2>&1; then cd testbed && gofmt -w .; else echo "go not installed; skipped testbed gofmt"; fi
	pnpm --dir web format

lint: ## Run static checks
	cd services/investigation && uv run ruff format --check .
	cd services/investigation && uv run ruff check .
	cd services/investigation && uv run mypy src tests
	cd services/tool-gateway && go vet ./...
	cd testbed && go vet ./...
	pnpm --dir web lint

test: test-python test-go test-web test-testbed ## Run all unit tests

test-python: ## Run Python tests
	cd services/investigation && uv run pytest

test-go: ## Run Go tests
	cd services/tool-gateway && go test -race ./...

test-web: ## Run web tests
	pnpm --dir web test

test-testbed: ## Run observable testbed tests
	cd testbed && go test -race ./...

test-integration: ## Run the Python-Go gateway contract against the live testbed
	./scripts/verify-stage2.sh

test-kind: ## Run Kubernetes connector acceptance in an ephemeral kind cluster
	./scripts/verify-stage2-kind.sh

acceptance-stage2: testbed-up testbed-smoke test lint build compose-config test-integration test-kind ## Run the complete stage-2 gate

test-stage3-restart: ## Verify Python restart recovery with PostgreSQL checkpoints
	cd services/investigation && uv run ../../scripts/verify-stage3.py

test-stage4-postgres: ## Verify pgvector retrieval and durable SSE event replay
	docker compose up -d --wait postgres
	cd services/investigation && uv run ../../scripts/verify-stage4.py

eval-offline: ## Run the five-case stage-3 Fake Model evaluation
	cd services/investigation && uv run ../../evals/run_stage3.py --mode fake

eval-online: ## Run the five-case stage-3 real-model evaluation (requires model env vars)
	mkdir -p artifacts
	cd services/investigation && uv run ../../evals/run_stage3.py --mode online --output ../../artifacts/stage3-online.json

eval-retrieval: ## Run the independent stage-4 Recall@K retrieval baseline
	mkdir -p artifacts
	cd services/investigation && uv run ../../evals/run_stage4_retrieval.py --catalog ../../knowledge/catalog.json --dataset ../../evals/stage4-retrieval-cases.json --output ../../artifacts/stage4-retrieval.json --markdown-output ../../artifacts/stage4-retrieval.md

acceptance-stage3: test lint test-stage3-restart eval-offline build compose-config ## Run the deterministic stage-3 gate

acceptance-stage4: test lint test-stage4-postgres eval-retrieval build compose-config ## Run the deterministic stage-4 gate

test-stage5-kind: ## Verify approval binding, idempotency, and isolated mutations
	./scripts/verify-stage5-kind.sh

acceptance-stage5: test lint proto-check build compose-config test-stage5-kind ## Run the complete stage-5 gate

build: ## Build all three services
	cd services/investigation && uv build
	cd services/tool-gateway && go build ./cmd/server
	cd testbed && go build ./cmd/service
	pnpm --dir web build

compose-up: ## Build and start the stage-0 stack
	docker compose up --build -d --wait

compose-down: ## Stop the local stack and keep database data
	docker compose down

compose-config: ## Validate the Compose model
	docker compose config --quiet
	docker compose -f testbed/compose.yaml config --quiet

testbed-up: ## Build and start the stage-1 testbed
	docker compose -f testbed/compose.yaml up --build -d --wait

testbed-down: ## Stop the stage-1 testbed and keep local data
	docker compose -f testbed/compose.yaml down

testbed-smoke: ## Submit one successful end-to-end checkout
	./testbed/scripts/smoke.sh

testbed-validate: ## Inject, verify, and recover all eight stage-1 faults
	./testbed/scripts/validate-scenarios.sh

clean: ## Remove generated local build outputs
	rm -rf services/investigation/dist web/dist web/coverage services/tool-gateway/server
