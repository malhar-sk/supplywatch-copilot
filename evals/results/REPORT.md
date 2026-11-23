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
| Refusal accuracy (not-in-corpus) | 1.000 | 1.000 |
| False-refusal rate (answerable) | 0.050 | 0.025 |
| /ask latency p50 (ms) | 3109 | 4247 |
| /ask latency p95 (ms) | 5682 | 6484 |
| retrieval-only p50 / p95 (ms) | 50 / 75 | 1172 / 1276 |
| answer tokens / question (in / out) | 2353 / 313 | 2330 / 318 |
| RAGAS judge tokens, total (in / out) | 986,754 / 248,571 | 978,394 / 248,446 |
| NaN RAGAS scores (faith / relev) | 0 / 0 | 0 / 0 |

- `baseline`: `python -m evals.run_eval --mode vector --tag baseline`; started 2026-11-22T12:24:38Z; 50 questions; answer model `claude-haiku-5-5` via `claude_cli`; judge `claude-haiku-5-5`
- `improved`: `python -m evals.run_eval --mode rerank --tag improved`; started 2026-11-22T12:46:50Z; 50 questions; answer model `claude-haiku-5-5` via `claude_cli`; judge `claude-haiku-5-5`
