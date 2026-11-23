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

- ~~Agent answer quality: there is no eval set for `/agent`, only routing tests with a mocked LLM
  and one recorded live run.~~ Measured on 2026-11-23; see "Eval round 2: agent eval" below.
- Latency with the Anthropic API directly (`LLM_BACKEND=anthropic`), because there was no API key on
  the build host. CLI-backend latency includes process start-up overhead.
- Real Telegram delivery, because no bot token was available.

---

# Eval round 2 (2026-11-23)

The sections below were added on 2026-11-23. None of the numbers above were changed.

## Agent eval (`/agent`, LangGraph analyst)

| Metric | Value |
|---|---|
| **Answer correctness** (Haiku judge, 0/1) | **0.70** (14 / 20) |
| Tool-selection precision, micro (distinct tools called that were expected) | 1.000 (23 / 23) |
| Tool-selection recall, micro (expected tools that were called) | 0.852 (23 / 27) |
| Tool-selection precision / recall, macro (per-question mean) | 0.850 / 0.816 |
| Exact tool-set match | 0.80 (16 / 20) |
| LLM turns per question, mean / max | 1.85 / 3 |
| Tool calls per question, mean / max | 1.20 / 4 |
| Latency per question p50 / p95 (ms, all uncached) | 7220 / 13623 |
| Agent tokens, total over 20 (in / out) | 63,874 / 15,430 |
| Judge tokens, total (in / out) | 23,911 / 1,957 |

| Question type (n) | Correctness | Micro precision | Micro recall | Exact set |
|---|---|---|---|---|
| single_tool (6) | 0.667 | 1.000 | 0.833 | 0.833 |
| multi_tool (6) | 0.667 | 1.000 | 0.929 | 0.833 |
| needs_documents (4) | 0.500 | 1.000 | 0.750 | 0.750 |
| unanswerable (4) | 1.000 | 1.000 | 0.667 | 0.750 |

- Dataset: `evals/agent_golden.jsonl`, 20 questions (6 single-tool, 6 multi-tool, 4 needs-documents,
  4 unanswerable). The set is **SYNTHETIC**: I wrote the questions by hand, and the reference answers
  and expected tools come from live tool outputs on 2026-11-23. Scores come from synthetic seed data,
  suppliers are fictional and documents are USGS text plus synthetic news. Every line has `"synthetic": true`.
- Command: `make eval-agent`, which runs `LLM_BACKEND=claude_cli python -m evals.agent_eval --agent-model claude-haiku-5-5`.
  It ran 12:06–12:09Z. The agent and the judge are both `claude-haiku-5-5` through the `claude_cli` backend, and
  the agent runs in-process with the same graph and tools as `POST /agent`. Judge verdicts are cached on disk
  (`LLM_CACHE_DIR`).
- Raw output: `evals/results/agent_eval.jsonl` (answer, tools and arguments, and the judge's reason for each
  question) and `evals/results/agent_eval_summary.json`.
- Definitions: tools are compared as sets of distinct names. For an unanswerable question, the expected tool is
  the lookup that would show the data is missing (empty for a20, a forecast question). If a question expects
  tools and the agent calls none, its macro precision is 0. That is why macro precision (0.85) is lower than
  micro precision (1.00).
- What failed (6 of 20):
  - **a04, a13:** Haiku called no tool and said "search_documents isn't available in this session". This is a
    real failure of Haiku with the CLI JSON tool protocol (`app/agent/chat_models.py`). It caused all four
    missed expected tools.
  - **a15:** the agent's search query did not retrieve the chunk that mentions the Section 301 tariff, so it
    said, correctly, that its excerpts did not mention one.
  - **a09, a11:** right tools, but the answer left out the December 2024 ban on exports to the US.
  - **a02:** a borderline judge call. The answer gives 73 → 74 (+1) and calls the trend flat, which matches the
    reference, but it also questions the tool's own summary. The judge's reason says the facts are "mostly
    present" yet scores 0. I left it as scored; with it counted correct, the score would be 0.75.
- n = 20, so one question moves correctness by 0.05. Treat it as a coarse signal. The default agent model is
  `claude-sonnet-5-5` (`AGENT_MODEL`); this eval used Haiku, as the plan asked, and Sonnet was NOT MEASURED.

## Retrieval ablation: vector vs hybrid vs hybrid + rerank (no LLM)

| Config | hit@5 | MRR@10 | hit@10 | retrieval p50 / p95 (ms) |
|---|---|---|---|---|
| vector (bge-small cosine) | 0.950 | **0.868** | 1.000 | 55 / 116 |
| hybrid (vector + Postgres FTS `ts_rank_cd`, RRF k=60), no reranker | 0.950 | 0.760 | 1.000 | 55 / 79 |
| hybrid + rerank (MiniLM cross-encoder on hybrid top-10) | **1.000** | 0.819 | 1.000 | 1133 / 1514 |

- Command: `make eval-ablation`, which runs `python -m evals.retrieval_ablation`. It ran on 2026-11-23 at
  11:24Z. The dataset is the same 50-question `evals/golden.jsonl`; ranking metrics use the 40 answerable
  questions, and latency uses all 50.
- Latency here is the in-process wall time of one `retrieve(k=10)` call (query embedding + SQL + rerank),
  measured after 3 warm-up queries on the 2 vCPU VPS. The earlier table measured `/tools/search` over HTTP,
  so the two sets of numbers are not directly comparable.
- The "BM25" side is Postgres full-text `ts_rank_cd`, which is BM25-like but not true BM25.
- Raw output: `evals/results/ablation.jsonl` and `evals/results/ablation_summary.json`.
- **Where the reranker's ~1.1 s pays off:** every config has the expected doc somewhere in the top 10
  (hit@10 = 1.0). The reranker's only hit@5 gain is on **2 of 40 questions** (q07 and q24), where vector search
  ranks the expected doc 7th and the reranker moves it to 3rd. Adding keyword search without a reranker hurts
  ranking (MRR@10 0.868 → 0.760, with no hit@5 change). The reranker recovers most of that loss (0.819) but stays
  below plain vector. So the reranker is worth it only when the answerer reads exactly the top 5; for top-10
  context, plain vector is as good and about 20× faster.

## Run-to-run variance of the improved config (answers + RAGAS)

| Metric | run 1 (`improved`, 2026-11-22) | run 2 | run 3 | mean | min–max (range) |
|---|---|---|---|---|---|
| **RAGAS faithfulness** | 0.892 | 0.938 | 0.937 | **0.922** | 0.892–0.938 (**0.046**) |
| RAGAS answer relevancy | 0.844 | 0.869 | 0.801 | 0.838 | 0.801–0.869 (0.069) |
| RAGAS context precision | 0.850 | 0.862 | 0.848 | 0.853 | 0.848–0.862 (0.015) |
| RAGAS context recall | 0.954 | 0.967 | 0.967 | 0.963 | 0.954–0.967 (0.013) |
| hit@5 / refusal accuracy / false-refusal | 1.0 / 1.0 / 0.025 | same | same | n/a | 0 |
| `/ask` p50 / p95 (ms) | 4247 / 6484 | 3402 / 6153 | 3503 / 5881 | 3717 / 6173 | n/a |

- Commands: `make eval-variance`, which runs `scripts/run_variance.sh 2 3` (in turn
  `python -m evals.run_eval --mode rerank --tag improved_rep{2,3}`) and then `python -m evals.variance_report`.
  Run 2 started at 11:25Z and run 3 at 11:45Z on 2026-11-23. Same 50 questions, with RAGAS on the 40
  answerable ones.
- Each repeat used a **fresh, empty LLM cache** (`LLM_CACHE_DIR=.llm_cache_rep{2,3}`), so every answer and every
  judge call was a new model call (0 cached judge calls in runs 2 and 3; run 1 had 167 of 441 cached). The
  `claude` CLI exposes no temperature setting, so this is the CLI's default sampling.
- Raw output: `evals/results/improved_rep{2,3}.jsonl`, `*_summary.json` and `evals/results/variance_summary.json`.
- **The original 0.883 → 0.892 faithfulness gap is within noise.** The same config, re-run, spans 0.046,
  about 5× the 0.009 gap. The baseline (0.883, one run) was **not** re-run, so its own spread is NOT MEASURED.
  It does fall below all three improved runs, but with one baseline run and three improved runs that is not
  evidence of a real difference. The honest summary: the reranker reliably improves **retrieval** (hit@5
  0.95 → 1.00, identical in all 3 runs), while any effect on faithfulness is too small to separate from
  answer and judge sampling noise at n = 40.
- Latency caveat: a 2-question agent smoke test (about 30 s) overlapped run 2's answer phase.
