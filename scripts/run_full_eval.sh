#!/usr/bin/env bash
# Full eval (both configs, with RAGAS) on the build host. Logs to logs/eval_full.log.
set -euo pipefail
cd "$(dirname "$0")/.."
export DATABASE_URL=${DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:15432/supplywatch}
export OMP_NUM_THREADS=1 LLM_CACHE_DIR="$PWD/.llm_cache"
PY=${PY:-.venv/bin/python}
$PY -m evals.run_eval --mode vector --tag baseline
$PY -m evals.run_eval --mode rerank --tag improved
$PY -m evals.report baseline improved
