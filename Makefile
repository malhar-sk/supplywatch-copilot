# SupplyWatch Copilot. On Windows without `make`, run the commands shown in TESTING.md.
PY ?= .venv/bin/python
export DATABASE_URL ?= postgresql+asyncpg://postgres:postgres@localhost:15432/supplywatch

.PHONY: up down logs seed test test-docker eval eval-baseline eval-improved corpus mcp simulate token

up:            ## build + start db, api, n8n
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

seed:          ## materials + synthetic history + RAG corpus ingest (inside the api container)
	docker compose exec api python scripts/seed.py

test:          ## full test suite on the host (needs `docker compose up -d db`); LLM is mocked
	LLM_BACKEND=fake $(PY) -m pytest -q

test-docker:   ## app tests inside the api container
	docker compose exec -e LLM_BACKEND=fake api python -m pytest -q tests

eval:          ## RAGAS + retrieval/refusal metrics, baseline vs improved
	$(PY) -m evals.run_eval --mode vector --tag baseline
	$(PY) -m evals.run_eval --mode hybrid --tag improved
	$(PY) -m evals.report

eval-retrieval: ## retrieval-only metrics (no LLM, fast)
	$(PY) -m evals.run_eval --mode vector --tag baseline --retrieval-only
	$(PY) -m evals.run_eval --mode hybrid --tag improved --retrieval-only

corpus:        ## rebuild data/corpus (fetches USGS PDFs + generates synthetic docs)
	$(PY) scripts/build_corpus.py

mcp:           ## run the MCP server on stdio (needs SUPPLYWATCH_API_KEY)
	$(PY) mcp_server/server.py

simulate:      ## force a threshold crossing -> signed webhook -> n8n -> alerts.csv
	$(PY) scripts/simulate_spike.py
