"""Evaluate the LangGraph analyst agent (/agent) on evals/agent_golden.jsonl (20 SYNTHETIC questions).

    LLM_BACKEND=claude_cli python -m evals.agent_eval            # agent + judge = claude-haiku-5-5
    python -m evals.agent_eval --agent-model claude-sonnet-5-5   # a different agent model

Runs agent.graph.run_agent in-process (same graph and tools as POST /agent) against the real database,
one question at a time, and measures:
  * tool selection: the set of distinct tools the agent called vs `expected_tools`.
    Micro precision = sum|called & expected| / sum|called|, micro recall = sum|called & expected| / sum|expected|,
    plus exact-set match rate. Questions with an empty expected set count only toward precision.
  * answer correctness: LLM judge (Haiku), 0/1 plus a one-line reason, against the reference answer.
    Judge calls go through core.llm, so they are cached on disk (LLM_CACHE_DIR) and re-runs are free.
  * steps: agent LLM turns (tool-call rounds + the final turn) and tool calls per question.
  * latency: wall time of run_agent per question, p50 / p95 (also reported over uncached runs only).
Writes evals/results/agent_eval.jsonl and evals/results/agent_eval_summary.json.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from evals.run_eval import RESULTS, pct  # noqa: E402

GOLDEN = ROOT / "evals" / "agent_golden.jsonl"

JUDGE_SYSTEM = """You grade answers from a supply-chain risk analyst agent against a reference answer.
Mark CORRECT (1) if the agent's answer states the key facts of the reference (numbers may differ slightly when the
reference says "about" or "roughly"; extra correct detail is fine) and contains no claim that contradicts it.
For questions the reference marks "Not answerable", mark CORRECT only if the agent says it cannot answer / the data
does not contain it, without inventing the missing fact (it may give related context from its tools).
Mark INCORRECT (0) if a key fact is missing or wrong, or if it invents an answer.
Reply with ONLY a JSON object: {"correct": 0 or 1, "reason": "<one short sentence>"}"""


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def tool_scores(called: list[str], expected: list[str]) -> dict:
    c, e = set(called), set(expected)
    tp = len(c & e)
    return {"tp": tp, "n_called": len(c), "n_expected": len(e),
            "precision": tp / len(c) if c else (1.0 if not e else 0.0),
            "recall": tp / len(e) if e else None,
            "exact": c == e}


def aggregate_tools(rows: list[dict]) -> dict:
    tp = sum(r["tool_scores"]["tp"] for r in rows)
    nc = sum(r["tool_scores"]["n_called"] for r in rows)
    ne = sum(r["tool_scores"]["n_expected"] for r in rows)
    recalls = [r["tool_scores"]["recall"] for r in rows if r["tool_scores"]["recall"] is not None]
    return {"micro_precision": round(tp / nc, 4) if nc else None,
            "micro_recall": round(tp / ne, 4) if ne else None,
            "macro_precision": round(statistics.mean(r["tool_scores"]["precision"] for r in rows), 4),
            "macro_recall": round(statistics.mean(recalls), 4) if recalls else None,
            "exact_set_match": round(statistics.mean(1.0 if r["tool_scores"]["exact"] else 0.0 for r in rows), 4)}


def parse_judgement(text: str) -> dict:
    t = text.strip()
    try:
        obj = json.loads(t[t.index("{"): t.rindex("}") + 1])
        correct = int(obj["correct"])
        if correct not in (0, 1):
            raise ValueError(correct)
        return {"correct": correct, "reason": str(obj.get("reason", ""))[:300]}
    except (ValueError, KeyError, TypeError):
        return {"correct": None, "reason": f"unparseable judge output: {t[:200]}"}


def judge(llm, item: dict, answer: str) -> dict:
    prompt = (f"QUESTION:\n{item['question']}\n\nREFERENCE ANSWER:\n{item['reference']}\n\n"
              f"AGENT ANSWER:\n{answer or '(empty)'}\n\nGrade it.")
    res = llm.complete(JUDGE_SYSTEM, prompt, 300)
    out = parse_judgement(res.text)
    out.update(judge_cached=res.cached, judge_input_tokens=res.input_tokens, judge_output_tokens=res.output_tokens)
    return out


def agent_model(name: str):
    from agent.chat_models import ChatClaudeCLI, KeywordRouterModel
    from core.config import get_settings
    from core.llm import resolve_backend

    b = resolve_backend()
    if b == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, api_key=get_settings().anthropic_api_key, max_tokens=1500, temperature=0)
    if b == "claude_cli":
        return ChatClaudeCLI(model=name)
    return KeywordRouterModel()  # fake/none: deterministic keyword router (pipeline check only)


async def evaluate(items: list[dict], model, judge_llm) -> list[dict]:
    from agent.graph import run_agent
    from core import llm as llm_mod

    # count agent LLM calls that came from the on-disk cache (cached runs have ~0 latency)
    cached_flags: list[bool] = []
    orig = llm_mod.TextLLM.complete

    def spy(self, *a, **kw):
        r = orig(self, *a, **kw)
        cached_flags.append(r.cached)
        return r

    rows = []
    llm_mod.TextLLM.complete = spy
    try:
        for it in items:
            cached_flags.clear()
            t0 = time.perf_counter()
            try:
                out = await run_agent(it["question"], model=model)
                err = None
            except Exception as e:  # recursion limit, CLI failure... recorded as a failed question
                out = {"answer": "", "trace": [], "usage": {}, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
                err = f"{type(e).__name__}: {str(e)[:200]}"
            called = [t["tool"] for t in out["trace"]]
            rounds = max((t["step"] for t in out["trace"]), default=0)
            row = {**it, "answer": out["answer"], "error": err, "tools_called": called,
                   "tool_args": [t["args"] for t in out["trace"]],
                   "tool_rounds": rounds, "n_tool_calls": len(called), "llm_turns": rounds + (0 if err else 1),
                   "latency_ms": out["latency_ms"], "usage": out.get("usage", {}),
                   "n_llm_calls_cached": sum(cached_flags), "n_llm_calls": len(cached_flags),
                   "tool_scores": tool_scores(called, it["expected_tools"])}
            row["any_cached"] = row["n_llm_calls_cached"] > 0
            cached_flags.clear()
            row["judge"] = judge(judge_llm, it, out["answer"])
            rows.append(row)
            print(f"  {it['id']} {it['type']:16} {row['latency_ms']:8.0f} ms  tools={called}  "
                  f"correct={row['judge']['correct']}", flush=True)
    finally:
        llm_mod.TextLLM.complete = orig
    return rows


def summarise(rows: list[dict]) -> dict:
    lat = [r["latency_ms"] for r in rows]
    unc = [r["latency_ms"] for r in rows if not r["any_cached"]]
    judged = [r for r in rows if r["judge"]["correct"] is not None]
    by_type = {}
    for t in dict.fromkeys(r["type"] for r in rows):
        sub = [r for r in rows if r["type"] == t]
        js = [r["judge"]["correct"] for r in sub if r["judge"]["correct"] is not None]
        by_type[t] = {"n": len(sub), "correctness": round(statistics.mean(js), 4) if js else None,
                      **aggregate_tools(sub)}
    return {"n_questions": len(rows), "n_errors": sum(1 for r in rows if r["error"]),
            "tool_selection": aggregate_tools(rows),
            "answer_correctness": round(statistics.mean(r["judge"]["correct"] for r in judged), 4) if judged else None,
            "n_judged": len(judged), "n_correct": sum(r["judge"]["correct"] for r in judged),
            "by_type": by_type,
            "steps": {"llm_turns_mean": round(statistics.mean(r["llm_turns"] for r in rows), 2),
                      "llm_turns_max": max(r["llm_turns"] for r in rows),
                      "tool_calls_mean": round(statistics.mean(r["n_tool_calls"] for r in rows), 2),
                      "tool_calls_max": max(r["n_tool_calls"] for r in rows)},
            "latency_ms": {"p50": pct(lat, 0.5), "p95": pct(lat, 0.95), "n": len(lat),
                           "p50_uncached": pct(unc, 0.5), "p95_uncached": pct(unc, 0.95), "n_uncached": len(unc)},
            "agent_tokens_total": {"input": sum(r["usage"].get("input_tokens", 0) for r in rows),
                                   "output": sum(r["usage"].get("output_tokens", 0) for r in rows)},
            "judge_tokens_total": {"input": sum(r["judge"]["judge_input_tokens"] for r in rows),
                                   "output": sum(r["judge"]["judge_output_tokens"] for r in rows),
                                   "cached_calls": sum(1 for r in rows if r["judge"]["judge_cached"])}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent-model", default=os.environ.get("EVAL_AGENT_MODEL", "claude-haiku-5-5"))
    ap.add_argument("--judge-model", default=os.environ.get("JUDGE_MODEL", "claude-haiku-5-5"))
    ap.add_argument("--tag", default="agent_eval")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    from core.llm import get_text_llm, resolve_backend

    items = load_golden()[: a.limit] if a.limit else load_golden()
    judge_llm = get_text_llm(model=a.judge_model)
    if judge_llm is None:
        raise SystemExit("no LLM backend for the judge (set LLM_BACKEND=claude_cli or anthropic)")
    started = datetime.now(timezone.utc).isoformat()
    rows = asyncio.run(evaluate(items, agent_model(a.agent_model), judge_llm))
    summary = {"tag": a.tag, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
               "command": "python -m evals.agent_eval " + " ".join(sys.argv[1:]), "golden": str(GOLDEN.name),
               "golden_size": len(items), "llm_backend": resolve_backend(), "agent_model": a.agent_model,
               "judge_model": a.judge_model, **summarise(rows)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    with (RESULTS / f"{a.tag}.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    (RESULTS / f"{a.tag}_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
