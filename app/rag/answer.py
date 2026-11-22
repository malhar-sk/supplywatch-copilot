"""Grounded answering: retrieve -> (refuse | LLM answer with [n] citations)."""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.llm import LLMResult, get_text_llm
from rag.retrieval import Hit, retrieve

REFUSAL = "I don't know based on the available documents."
SYSTEM = (
    "You are SupplyWatch Copilot, an analyst for critical-mineral supply risk. Answer ONLY from the "
    "numbered context passages. Cite every claim with the passage number in square brackets, e.g. [2]. "
    "Be concise (at most 5 sentences). If the passages do not contain the answer, reply with exactly: "
    f"\"{REFUSAL}\" Passages marked (synthetic) describe fictional demo companies; you may use them."
)


@dataclass
class AskResult:
    answer: str
    refused: bool
    citations: list[dict]
    contexts: list[str]
    retrieval_mode: str
    top_similarity: float
    retrieved_doc_ids: list[str] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    timings_ms: dict = field(default_factory=dict)


def build_prompt(question: str, hits: list[Hit]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        tag = " (synthetic)" if h.synthetic else ""
        blocks.append(f"[{i}] {h.title}{tag}\n{h.text.split(chr(10), 1)[-1]}")
    return "Context passages:\n\n" + "\n\n".join(blocks) + f"\n\nQuestion: {question}\nAnswer:"


def is_refusal(answer: str) -> bool:
    a = answer.strip().lower()
    return a.startswith("i don't know") or a.startswith("i do not know") or REFUSAL.lower() in a


def extractive_answer(question: str, hits: list[Hit]) -> str:
    """No-LLM fallback: the two sentences from the top passages that share the
    most words with the question, each cited."""
    q = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", question) if len(w) > 3}
    scored = []
    for i, h in enumerate(hits[:3], 1):
        for s in re.split(r"(?<=[.!?])\s+", h.text.split("\n", 1)[-1]):
            overlap = len(q & {w.lower() for w in re.findall(r"[A-Za-z0-9]+", s)})
            scored.append((overlap, -i, s.strip(), i))
    best = sorted(scored, reverse=True)[:2]
    return " ".join(f"{s} [{i}]" for _, _, s, i in best) + " (extractive answer: no LLM configured)"


def cited_numbers(answer: str) -> list[int]:
    return sorted({int(n) for n in re.findall(r"\[(\d+)\]", answer)})


async def ask(db: AsyncSession, question: str, material: str | None = None, mode: str | None = None,
              k: int | None = None, backend: str | None = None) -> AskResult:
    s = get_settings()
    mode = mode or s.retrieval_mode
    k = k or s.retrieval_top_k
    t0 = time.perf_counter()
    hits = await retrieve(db, question, k=k, mode=mode, material=material)
    t_ret = (time.perf_counter() - t0) * 1000
    top_sim = max((h.similarity for h in hits), default=0.0)
    contexts = [h.text for h in hits]

    if not hits or top_sim < s.refusal_min_similarity:
        return AskResult(answer=REFUSAL + " Nothing in the SupplyWatch corpus is relevant enough to answer that.",
                         refused=True, citations=[], contexts=contexts, retrieval_mode=mode,
                         top_similarity=round(top_sim, 4), retrieved_doc_ids=[h.document_id for h in hits], usage={"input_tokens": 0, "output_tokens": 0},
                         timings_ms={"retrieval": round(t_ret, 1), "llm": 0.0})

    llm = get_text_llm(backend=backend)
    t1 = time.perf_counter()
    if llm is None:
        answer, res = extractive_answer(question, hits), LLMResult(text="", model="none")
    else:
        res = await asyncio.to_thread(llm.complete, SYSTEM, build_prompt(question, hits), 600)
        answer = res.text.strip()
    t_llm = (time.perf_counter() - t1) * 1000
    refused = is_refusal(answer)
    used = cited_numbers(answer)
    citations = [h.as_citation(i) for i, h in enumerate(hits, 1) if not refused and (i in used or not used)]
    return AskResult(answer=answer, refused=refused, citations=citations, contexts=contexts, retrieval_mode=mode,
                     top_similarity=round(top_sim, 4), retrieved_doc_ids=[h.document_id for h in hits],
                     usage={"model": res.model, "input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                            "cached": res.cached},
                     timings_ms={"retrieval": round(t_ret, 1), "llm": round(t_llm, 1)})
