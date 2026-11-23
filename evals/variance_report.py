"""Run-to-run variance of the improved config: the original run (`improved`) plus repeats
(`improved_rep2`, `improved_rep3`, made by scripts/run_variance.sh with fresh LLM caches).

    python -m evals.variance_report     # writes evals/results/variance_summary.json and prints a table
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parent / "results"
TAGS = ["improved", "improved_rep2", "improved_rep3"]
METRICS = {
    "faithfulness": lambda s: s["ragas"]["faithfulness"],
    "answer_relevancy": lambda s: s["ragas"]["answer_relevancy"],
    "context_precision": lambda s: s["ragas"]["context_precision"],
    "context_recall": lambda s: s["ragas"]["context_recall"],
    "hit_at_5": lambda s: s["hit_at_5"],
    "refusal_accuracy": lambda s: s["refusal_accuracy"],
    "false_refusal_rate": lambda s: s["false_refusal_rate"],
    "ask_latency_p50_ms": lambda s: s["latency_ms"]["p50"],
    "ask_latency_p95_ms": lambda s: s["latency_ms"]["p95"],
}


def spread(values: list[float | None]) -> dict:
    v = [x for x in values if x is not None]
    lo, hi = min(v), max(v)
    return {"n": len(v), "mean": round(statistics.mean(v), 4), "min": lo, "max": hi,
            "range": round(hi - lo, 4), "half_range": round((hi - lo) / 2, 4)}


def main(tags: list[str]) -> dict:
    runs = {t: json.loads((RESULTS / f"{t}_summary.json").read_text(encoding="utf-8")) for t in tags
            if (RESULTS / f"{t}_summary.json").exists()}
    out = {"runs": {t: {"started_at": s["started_at"], "command": s["command"],
                        "judge_cached_calls": s["ragas"]["judge_cached_calls"], "judge_calls": s["ragas"]["judge_calls"],
                        **{m: f(s) for m, f in METRICS.items()}} for t, s in runs.items()},
           "spread": {m: spread([fn(s) for s in runs.values()]) for m, fn in METRICS.items()}}
    base = RESULTS / "baseline_summary.json"
    if base.exists():
        b = json.loads(base.read_text(encoding="utf-8"))["ragas"]["faithfulness"]
        fa = out["spread"]["faithfulness"]
        out["baseline_faithfulness_single_run"] = b
        out["gap_mean_minus_baseline"] = round(fa["mean"] - b, 4)
        out["baseline_inside_improved_range"] = fa["min"] <= b <= fa["max"]
    (RESULTS / "variance_summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("| Metric | " + " | ".join(runs) + " | mean | min-max (range) |")
    print("|---|" + "---|" * (len(runs) + 2))
    for m in METRICS:
        sp = out["spread"][m]
        print(f"| {m} | " + " | ".join(str(out["runs"][t][m]) for t in runs)
              + f" | {sp['mean']} | {sp['min']}-{sp['max']} ({sp['range']}) |")
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1:] or TAGS)["spread"]["faithfulness"]))
