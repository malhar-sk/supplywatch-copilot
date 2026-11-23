"""Retrieval ablation (no LLM, free): vector vs hybrid (BM25-style FTS + vector, RRF, no reranker)
vs hybrid + cross-encoder rerank, on the 50-question golden set.

    python -m evals.retrieval_ablation            # writes evals/results/ablation_summary.json

Retrieves the top 10 chunks per question in-process (calls rag.retrieval.retrieve directly, no HTTP),
then computes, over the 40 answerable questions:
  hit@5   any expected doc among the first 5 chunks
  MRR@10  1 / rank of the first chunk from an expected doc within the first 10 (0 if none)
  hit@10  any expected doc among the first 10
Latency = wall time of one retrieve() call (query embedding + SQL + rerank where used), measured on all
50 questions after 3 warm-up queries per mode. Modes are run one after another, never concurrently.
Note: rerank re-orders the hybrid top-10 (RERANK_CANDIDATES=10), so its hit@10 equals hybrid's by design.
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from evals.run_eval import GOLDEN, RESULTS, pct  # noqa: E402

MODES = ["vector", "hybrid", "rerank"]


def rank_metrics(retrieved: list[str], expected: list[str]) -> dict:
    ranks = [i for i, d in enumerate(retrieved[:10], 1) if d in expected]
    first = ranks[0] if ranks else None
    return {"hit_at_5": 1.0 if first and first <= 5 else 0.0,
            "hit_at_10": 1.0 if first else 0.0,
            "rr_at_10": 1.0 / first if first else 0.0}


def summarise(rows: list[dict]) -> dict:
    ans = [r for r in rows if r["type"] != "not_in_corpus"]
    lat = [r["latency_ms"] for r in rows]
    return {"n_questions": len(rows), "n_answerable": len(ans),
            "hit_at_5": round(statistics.mean(r["hit_at_5"] for r in ans), 4),
            "mrr_at_10": round(statistics.mean(r["rr_at_10"] for r in ans), 4),
            "hit_at_10": round(statistics.mean(r["hit_at_10"] for r in ans), 4),
            "latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "max": round(max(lat), 1)}}


async def run() -> dict:
    from core.database import AsyncSessionLocal
    from rag.retrieval import retrieve

    items = [json.loads(x) for x in GOLDEN.read_text(encoding="utf-8").splitlines() if x.strip()]
    out = {"started_at": datetime.now(timezone.utc).isoformat(), "golden_size": len(items),
           "command": "python -m evals.retrieval_ablation", "k": 10, "modes": {}}
    per_q = []
    async with AsyncSessionLocal() as db:
        for mode in MODES:
            for it in items[:3]:  # warm-up: model loads, DB caches
                await retrieve(db, it["question"], k=10, mode=mode)
            rows = []
            for it in items:
                t0 = time.perf_counter()
                hits = await retrieve(db, it["question"], k=10, mode=mode)
                ms = (time.perf_counter() - t0) * 1000
                docs = [h.document_id for h in hits]
                rows.append({"id": it["id"], "type": it["type"], "mode": mode, "latency_ms": round(ms, 1),
                             "retrieved_doc_ids": docs, **rank_metrics(docs, it.get("expected_docs") or [])})
            out["modes"][mode] = summarise(rows)
            per_q += rows
            print(mode, json.dumps(out["modes"][mode]), flush=True)
    out["finished_at"] = datetime.now(timezone.utc).isoformat()
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / "ablation.jsonl").open("w", encoding="utf-8") as f:
        for r in per_q:
            f.write(json.dumps(r) + "\n")
    (RESULTS / "ablation_summary.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), indent=2))
