"""Render evals/results/*_summary.json into evals/results/REPORT.md (a markdown table)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"


def f(x, nd=3):
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


def main(tags: list[str]) -> str:
    rows = []
    for t in tags:
        p = RESULTS / f"{t}_summary.json"
        if p.exists():
            rows.append(json.loads(p.read_text(encoding="utf-8")))
    if not rows:
        return "No full eval summaries found."
    metrics = [
        ("Retrieval mode", lambda s: s["mode"]),
        ("RAGAS faithfulness", lambda s: f(s.get("ragas", {}).get("faithfulness"))),
        ("RAGAS answer relevancy", lambda s: f(s.get("ragas", {}).get("answer_relevancy"))),
        ("RAGAS context precision", lambda s: f(s.get("ragas", {}).get("context_precision"))),
        ("RAGAS context recall", lambda s: f(s.get("ragas", {}).get("context_recall"))),
        ("hit@5 (answerable)", lambda s: f(s["hit_at_5"])),
        ("recall@5 (all expected docs)", lambda s: f(s["recall_at_5"])),
        ("MRR@5", lambda s: f(s["mrr_at_5"])),
        ("Refusal accuracy (not-in-corpus)", lambda s: f(s.get("refusal_accuracy"))),
        ("False-refusal rate (answerable)", lambda s: f(s.get("false_refusal_rate"))),
        ("/ask latency p50 (ms)", lambda s: f(s["latency_ms"]["p50"], 0)),
        ("/ask latency p95 (ms)", lambda s: f(s["latency_ms"]["p95"], 0)),
        ("retrieval-only p50 / p95 (ms)",
         lambda s: f"{f(s['latency_ms']['retrieval_p50'], 0)} / {f(s['latency_ms']['retrieval_p95'], 0)}"),
        ("answer tokens / question (in / out)",
         lambda s: f"{s['answer_tokens_per_question']['input_mean']:.0f} / {s['answer_tokens_per_question']['output_mean']:.0f}"),
        ("RAGAS judge tokens, total (in / out)",
         lambda s: f"{s.get('ragas', {}).get('judge_input_tokens', 0):,} / {s.get('ragas', {}).get('judge_output_tokens', 0):,}"),
        ("NaN RAGAS scores (faith / relev)",
         lambda s: f"{s.get('ragas', {}).get('faithfulness_nan', 'n/a')} / {s.get('ragas', {}).get('answer_relevancy_nan', 'n/a')}"),
    ]
    head = "| Metric | " + " | ".join(r["tag"] for r in rows) + " |"
    sep = "|---|" + "---|" * len(rows)
    lines = [head, sep] + [f"| {name} | " + " | ".join(fn(r) for r in rows) + " |" for name, fn in metrics]
    meta = "\n".join(f"- `{r['tag']}`: `{r['command']}`; started {r['started_at'][:19]}Z; {r['golden_size']} questions; "
                     f"answer model `{r['answer_model']}` via `{r['llm_backend']}`; judge `{r.get('ragas', {}).get('judge_model')}`"
                     for r in rows)
    md = "\n".join(lines) + "\n\n" + meta + "\n"
    (RESULTS / "REPORT.md").write_text(md, encoding="utf-8")
    return md


if __name__ == "__main__":
    print(main(sys.argv[1:] or ["baseline", "improved"]))
