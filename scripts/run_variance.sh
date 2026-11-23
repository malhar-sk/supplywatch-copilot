#!/usr/bin/env bash
# Re-run the improved config (answers + RAGAS) N more times, each with a fresh, empty LLM cache,
# so every answer and every judge call is a new model call. Usage: scripts/run_variance.sh 2 3
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
for i in "$@"; do
  export LLM_CACHE_DIR="$PWD/.llm_cache_rep$i"
  rm -rf "$LLM_CACHE_DIR"
  echo "=== rep $i (cache $LLM_CACHE_DIR) $(date -u +%FT%TZ)"
  $PY -m evals.run_eval --mode rerank --tag "improved_rep$i"
done
