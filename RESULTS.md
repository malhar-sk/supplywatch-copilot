# RESULTS

Only numbers that were actually measured are listed here. Each one gives the command that produced
it, the date, and the dataset size. Raw per-question outputs are in `evals/results/*.jsonl`, and
summaries are in `evals/results/*_summary.json`.

## Headline: baseline vs improved

| Metric | Baseline (plain vector) | Improved (hybrid + cross-encoder rerank) |
|---|---|---|
| **RAGAS faithfulness** | **0.883** | **0.892** |
| **Retrieval hit@5** (answerable questions) | **0.950** | **1.000** |
| **`/ask` latency p95** (ms, end-to-end incl. LLM) | **5683** | **6484** |

- Dataset: `evals/golden.jsonl`, 50 questions (22 factual, 8 multi-doc, 10 supplier exposure,
  10 not-in-corpus). The golden set was built by `evals/build_golden.py`. Reference answers are
  LLM-drafted from the corpus documents, so they are **synthetic labels**, not human-labelled.
- Corpus: 203 documents → 210 chunks. 154 are real USGS Mineral Commodity Summaries sections and
  49 are **synthetic** supplier/news docs.
- Date: 2026-11-22 (UTC). Baseline started 12:24:38Z, improved started 12:46:50Z.
- Commands:
  - Baseline: `python -m evals.run_eval --mode vector --tag baseline`
  - Improved: `python -m evals.run_eval --mode rerank --tag improved`
  - Table: `python -m evals.report` (or `make eval`, which runs all three)
- Answer model: `claude-haiku-5-5`, called through the `claude_cli` backend (the build host has no
  API key; see DECISIONS.md #6). RAGAS judge: `claude-haiku-5-5`. Answer embeddings for answer
  relevancy: local bge-small.

## Full comparison

| Metric | baseline | improved |
|---|---|---|
| Retrieval mode | vector | rerank |
| RAGAS faithfulness | 0.883 | 0.892 |
| RAGAS answer relevancy | 0.818 | 0.844 |
| RAGAS context precision | 0.847 | 0.850 |
| RAGAS context recall | 0.921 | 0.954 |
| hit@5 (answerable) | 0.950 | 1.000 |
| recall@5 (all expected docs) | 0.942 | 0.975 |
| MRR@5 | 0.861 | 0.819 |
| hit@5 by type: factual / multi-doc / exposure | 0.955 / 0.875 / 1.000 | 1.000 / 1.000 / 1.000 |
| Refusal accuracy (10 not-in-corpus questions) | 1.000 | 1.000 |
| False-refusal rate (40 answerable questions) | 0.050 | 0.025 |
| `/ask` latency p50 (ms) | 3109 | 4247 |
| `/ask` latency p95 (ms) | 5683 | 6484 |
| Retrieval-only latency p50 / p95 (ms) | 50 / 75 | 1172 / 1276 |
| Answer tokens per question, mean (in / out) | 2353 / 313 | 2330 / 318 |
| Answer tokens, total over 50 questions (in / out) | 117,649 / 15,655 | 116,502 / 15,927 |
| RAGAS judge tokens, total (in / out) | 986,754 / 248,571 | 978,394 / 248,446 |
| RAGAS judge calls (cached) | 443 (3) | 441 (167) |
| NaN RAGAS scores (faithfulness / relevancy / precision) | 0 / 0 / 1 | 0 / 0 / 0 |

Notes:
- RAGAS metrics are computed on the 40 answerable questions. The refusal metrics use all 50.
- Latency was measured on the 2 vCPU build VPS, with the API in Docker and the reranker on CPU.
  In the improved run, one question's answer came from the on-disk LLM cache. The uncached-only
  figures are p50 4279 ms and p95 6514 ms (n=49).
- The improved configuration buys +0.05 hit@5 and +0.03 context recall at a cost of about 1.1 s of
  CPU reranking per query. MRR@5 drops slightly, so the reranker sometimes moves the expected doc
  from rank 1 to ranks 2–5.
- **Selection caveat:** three retrieval configurations were tried on this same 50-question set
  before choosing the "improved" one (DECISIONS.md #11). There is no separate dev/test split, so the
  gain is optimistic.
- Cost in USD: NOT MEASURED. Calls went through the `claude` CLI rather than a metered API key.
  Token counts are reported instead.

## Retrieval-only runs (no LLM), used to choose the improvement

Date 2026-11-22. 50 questions each.

| Mode | Command | hit@5 | recall@5 | MRR@5 | search p50 / p95 (ms) |
|---|---|---|---|---|---|
| vector | `python -m evals.run_eval --mode vector --tag vector --retrieval-only` | 0.950 | 0.942 | 0.861 | 58 / 75 |
| hybrid (vector + Postgres FTS, RRF) | `python -m evals.run_eval --mode hybrid --tag hybrid --retrieval-only` | 0.950 | 0.925 | 0.778 | 65 / 90 |
| rerank (hybrid top-10 → MiniLM cross-encoder) | `python -m evals.run_eval --mode rerank --tag rerank --retrieval-only` | 1.000 | 0.975 | 0.819 | 1219 / 1733 |

## Other recorded runs

| What | Result | Command | Date | Evidence |
|---|---|---|---|---|
| Test suite (LLM mocked, pgvector db in Docker) | 93 passed | `LLM_BACKEND=fake .venv/bin/python -m pytest -q` (`make test`) | 2026-11-23 | n/a |
| Webhook → n8n → CSV | Gallium 79 → 97 crossed threshold 89. 2 alerts fired: this run's subscription plus one left over from an earlier run. Signed delivery succeeded on attempt 1 (HTTP 200) and n8n appended the rows to `alerts.csv` | `python scripts/simulate_spike.py` | 2026-11-22 | `examples/simulate_spike_output.txt`, `n8n_data/alerts.sample.csv` |
| Telegram branch | Mocked (no `TELEGRAM_BOT_TOKEN`); message written to `n8n_data/telegram_mock.log` | same | 2026-11-22 | `examples/telegram_mock.sample.log` |
| Live agent run (LangGraph, 5 tools) | Final answer plus tool-call trace | `python scripts/record_agent_run.py` | 2026-11-22 | `examples/agent_run.json` |

## NOT MEASURED

- Agent answer quality: there is no eval set for `/agent`, only routing tests with a mocked LLM
  and one recorded live run.
- Latency with the Anthropic API directly (`LLM_BACKEND=anthropic`), because there was no API key on
  the build host. CLI-backend latency includes process start-up overhead.
- Real Telegram delivery, because no bot token was available.
