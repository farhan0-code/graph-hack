.PHONY: hydra-up hydra-down verify test lint index seed graph-stats entities benchmark judge export-answers aggregate perf envelope api

export UID := $(shell id -u)
export GID := $(shell id -g)

hydra-up:
	mkdir -p falkordb-data/cache falkordb-data/minio
	printf '%s\n' 'local-development-token-32-bytes' > falkordb-data/auth-token
	docker compose up -d
	uv run python scripts/verify/wait_for_falkordb.py

hydra-down:
	docker compose down

verify:
	uv run python scripts/verify/verify_falkordb.py

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

index:
	uv run python scripts/ingest/index_corpus.py

graph-stats:
	uv run python scripts/ingest/graph_stats.py

seed:
	uv run python scripts/ingest/seed_graph.py

entities:
	uv run python scripts/ingest/resolve_entities.py

benchmark:
	uv run python scripts/benchmark/run_benchmark.py

judge:
	uv run python scripts/benchmark/judge_answers.py

export-answers:
	uv run python scripts/benchmark/export_answers.py

aggregate:
	uv run python scripts/benchmark/aggregate_runs.py

perf:
	uv run python scripts/benchmark/falkordb_perf.py

envelope:
	uv run python scripts/benchmark/safety_envelope.py

api:
	uv run uvicorn canon_api.app:app --reload --port 8000
