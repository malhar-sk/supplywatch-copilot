"""Evaluate /ask on evals/golden.jsonl.

  python -m evals.run_eval --mode vector --tag baseline
  python -m evals.run_eval --mode hybrid --tag improved
  python -m evals.run_eval --mode hybrid --tag improved --retrieval-only   # no LLM at all

What it does
  1. starts the API (uvicorn, on the host) unless --api-url is given
  2. sends every golden question to POST /ask (sequentially; latency = HTTP round trip)
  3. own metrics: hit@5 (answerable questions: any expected doc in the 5 retrieved),
     MRR@5, refusal accuracy on not_in_corpus questions, false-refusal rate on answerable ones
  4. RAGAS (answerable questions): faithfulness, answer relevancy, context precision
     (with reference), context recall. Judge = Haiku-class model, embeddings = local bge-small.
  5. writes evals/results/<tag>.jsonl (per question) and evals/results/<tag>_summary.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
GOLDEN = ROOT / "evals" / "golden.jsonl"
RESULTS = ROOT / "evals" / "results"


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def start_api() -> tuple[subprocess.Popen, str]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "ENABLE_SCHEDULER": "false"}
    log = open(ROOT / "logs" / "eval_api.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port)],
                            cwd=ROOT / "app", env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    for _ in range(180):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                return proc, url
        except httpx.HTTPError:
            time.sleep(0.5)
    proc.kill()
    raise SystemExit("API did not start; see logs/eval_api.log")


def run_questions(url: str, items: list[dict], mode: str, retrieval_only: bool) -> list[dict]:
    key = httpx.post(f"{url}/auth/keys", json={"company_name": "eval", "tier": "pro"}).json()["data"]["api_key"]
    h = {"X-API-Key": key}
    out = []
    with httpx.Client(timeout=300, headers=h) as c:
        for it in items:
            t0 = time.perf_counter()
            if retrieval_only:
                r = c.post(f"{url}/tools/search", json={"query": it["question"], "k": 5, "mode": mode})
                d = r.json()["data"]
                rec = {"retrieved_doc_ids": [x["document_id"] for x in d["results"]],
                       "top_similarity": max((x["similarity"] for x in d["results"]), default=0)}
            else:
                r = c.post(f"{url}/ask", json={"question": it["question"], "mode": mode, "include_contexts": True})
                r.raise_for_status()
                d = r.json()["data"]
                rec = {k: d[k] for k in ("answer", "refused", "contexts", "retrieved_doc_ids", "top_similarity",
                                         "usage", "timings_ms")}
                rec["n_citations"] = len(d["citations"])
            rec["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            out.append({**it, **rec})
            print(f"  {it['id']} {it['type']:14} {rec['latency_ms']:8.0f} ms  refused={rec.get('refused')}", flush=True)
    return out


def group_expected(expected: list[str], qtype: str) -> list[list[str]]:
    """factual: all expected ids are interchangeable editions -> one group.
    multi_doc / exposure: every expected doc is needed -> one group each."""
    return [expected] if qtype == "factual" else [[d] for d in expected]


def own_metrics(rows: list[dict], retrieval_only: bool) -> dict:
    ans = [r for r in rows if r["type"] != "not_in_corpus"]
    nic = [r for r in rows if r["type"] == "not_in_corpus"]
    hits, rr, rec = [], [], []
    for r in ans:
        top = r["retrieved_doc_ids"][:5]
        ranks = [i for i, d in enumerate(top, 1) if d in r["expected_docs"]]
        hits.append(1.0 if ranks else 0.0)
        rr.append(1.0 / ranks[0] if ranks else 0.0)
        # twins (2024/2025 editions of the same section) count once: a group is found if any member is
        groups = group_expected(r["expected_docs"], r["type"])
        rec.append(sum(1 for g in groups if set(g) & set(top)) / len(groups))
    m = {"n_questions": len(rows), "n_answerable": len(ans), "n_not_in_corpus": len(nic),
         "hit_at_5": round(statistics.mean(hits), 4), "mrr_at_5": round(statistics.mean(rr), 4),
         "recall_at_5": round(statistics.mean(rec), 4)}
    by_type = {}
    for t in sorted({r["type"] for r in ans}):
        sub = [h for h, r in zip(hits, ans) if r["type"] == t]
        by_type[t] = round(statistics.mean(sub), 4)
    m["hit_at_5_by_type"] = by_type
    if not retrieval_only:
        m["refusal_accuracy"] = round(statistics.mean(1.0 if r["refused"] else 0.0 for r in nic), 4) if nic else None
        m["false_refusal_rate"] = round(statistics.mean(1.0 if r["refused"] else 0.0 for r in ans), 4)
        lat = [r["latency_ms"] for r in rows]
        unc = [r["latency_ms"] for r in rows if not r.get("usage", {}).get("cached")]
        m["latency_ms"] = {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "n": len(lat),
                           "p50_uncached_llm": pct(unc, 0.5), "p95_uncached_llm": pct(unc, 0.95), "n_uncached": len(unc),
                           "retrieval_p50": pct([r["timings_ms"]["retrieval"] for r in rows], 0.5),
                           "retrieval_p95": pct([r["timings_ms"]["retrieval"] for r in rows], 0.95)}
        ti = [r["usage"].get("input_tokens", 0) for r in rows]
        to = [r["usage"].get("output_tokens", 0) for r in rows]
        m["answer_tokens_per_question"] = {"input_mean": round(statistics.mean(ti), 1),
                                           "output_mean": round(statistics.mean(to), 1),
                                           "input_total": sum(ti), "output_total": sum(to)}
    else:
        lat = [r["latency_ms"] for r in rows]
        m["search_latency_ms"] = {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95)}
    return m


def ragas_metrics(rows: list[dict], judge_model: str) -> dict:
    from ragas import EvaluationDataset, RunConfig, SingleTurnSample, evaluate
    from ragas.embeddings import LangchainEmbeddingsWrapper
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall, ResponseRelevancy

    from evals.ragas_llm import JudgeChat, LocalEmbeddings, TokenCounter

    ans = [r for r in rows if r["type"] != "not_in_corpus"]
    samples = [SingleTurnSample(user_input=r["question"], response=r["answer"],
                                retrieved_contexts=r["contexts"] or ["(no context retrieved)"],
                                reference=r["reference"]) for r in ans]
    counter = TokenCounter()
    llm = LangchainLLMWrapper(JudgeChat(model=judge_model, counter=counter))
    emb = LangchainEmbeddingsWrapper(LocalEmbeddings())
    t0 = time.perf_counter()
    res = evaluate(dataset=EvaluationDataset(samples=samples),
                   metrics=[Faithfulness(), ResponseRelevancy(), LLMContextPrecisionWithReference(), LLMContextRecall()],
                   llm=llm, embeddings=emb, show_progress=True,
                   run_config=RunConfig(max_workers=int(os.environ.get("RAGAS_WORKERS", "3")), timeout=900,
                                        max_retries=3))
    df = res.to_pandas()
    cols = {"faithfulness": "faithfulness", "answer_relevancy": "answer_relevancy",
            "llm_context_precision_with_reference": "context_precision", "context_recall": "context_recall"}
    out = {"judge_model": judge_model, "n_samples": len(samples), "ragas_wall_s": round(time.perf_counter() - t0, 1)}
    per_q = {}
    for col, name in cols.items():
        if col not in df:
            out[name] = None
            continue
        vals = [float(v) for v in df[col].tolist()]
        ok = [v for v in vals if not math.isnan(v)]
        out[name] = round(statistics.mean(ok), 4) if ok else None
        out[f"{name}_nan"] = len(vals) - len(ok)
        for r, v in zip(ans, vals):
            per_q.setdefault(r["id"], {})[name] = None if math.isnan(v) else round(v, 4)
    out.update(counter.as_dict())
    return out, per_q


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["vector", "hybrid", "rerank"], required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--api-url")
    ap.add_argument("--retrieval-only", action="store_true")
    ap.add_argument("--no-ragas", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "claude-haiku-5-5"))
    a = ap.parse_args()

    items = [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()]
    if a.limit:
        items = items[: a.limit]
    RESULTS.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    proc = None
    url = a.api_url
    if not url:
        proc, url = start_api()
    try:
        started = datetime.now(timezone.utc).isoformat()
        rows = run_questions(url, items, a.mode, a.retrieval_only)
    finally:
        if proc:
            proc.terminate()
            proc.wait(15)
    from core.llm import resolve_backend
    from core.config import get_settings

    summary = {"tag": a.tag, "mode": a.mode, "started_at": started, "golden_size": len(items),
               "command": "python -m evals.run_eval " + " ".join(sys.argv[1:]),
               "llm_backend": resolve_backend(), "answer_model": get_settings().answer_model,
               "retrieval_only": a.retrieval_only, **own_metrics(rows, a.retrieval_only)}
    if not (a.retrieval_only or a.no_ragas):
        r, per_q = ragas_metrics(rows, a.judge_model)
        summary["ragas"] = r
        for row in rows:
            row["ragas"] = per_q.get(row["id"])
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    suffix = "_retrieval" if a.retrieval_only else ""
    with (RESULTS / f"{a.tag}{suffix}.jsonl").open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    (RESULTS / f"{a.tag}{suffix}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
