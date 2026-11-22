"""Retrieval over pgvector.

mode="vector"  baseline: cosine nearest neighbours on bge-small embeddings.
mode="hybrid"  vector top-N  +  Postgres full-text (BM25-like ts_rank_cd over an
               OR-query of the question's terms) top-N, fused with Reciprocal Rank Fusion (k=60).
mode="rerank"  the top RERANK_CANDIDATES (10) hybrid results re-scored by a small
               cross-encoder (ms-marco-MiniLM-L-6-v2, 320-token inputs); keep the best k.

Every hit carries `similarity` (cosine similarity to the query), which the
answerer uses for the refusal decision regardless of mode.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rag.embeddings import embed_query

CANDIDATES = 20
RRF_K = 60
_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass
class Hit:
    chunk_id: int
    document_id: str
    title: str
    source: str
    source_url: str
    synthetic: bool
    text: str
    similarity: float
    score: float  # ranking score in the chosen mode (cosine or RRF)

    def as_citation(self, n: int, snippet_chars: int = 280) -> dict:
        body = self.text.split("\n", 1)[-1]
        d = asdict(self)
        d.pop("text")
        d.update(n=n, snippet=body[:snippet_chars] + ("..." if len(body) > snippet_chars else ""))
        return d


def _vec_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + "]"


def or_query(question: str) -> str:
    terms = [w.lower() for w in _WORD.findall(question) if len(w) > 2]
    return " | ".join(dict.fromkeys(terms))


_SELECT = """
SELECT c.id, c.document_id, d.title, d.source, d.source_url, d.synthetic, c.text,
       1 - (c.embedding <=> CAST(:q AS vector)) AS similarity
FROM rag_chunks c JOIN rag_documents d ON d.id = c.document_id
"""


def _material_filter(material: str | None) -> tuple[str, dict]:
    if not material:
        return "", {}
    return " AND (d.material ILIKE :mat OR d.material = 'multiple')", {"mat": f"%{material}%"}


async def vector_search(db: AsyncSession, qvec: str, k: int, material: str | None = None) -> list[Hit]:
    where, params = _material_filter(material)
    sql = _SELECT + f" WHERE true {where} ORDER BY c.embedding <=> CAST(:q AS vector) LIMIT :k"
    rows = (await db.execute(text(sql), {"q": qvec, "k": k, **params})).all()
    return [Hit(*r[:7], similarity=float(r[7]), score=float(r[7])) for r in rows]


async def keyword_search(db: AsyncSession, question: str, qvec: str, k: int, material: str | None = None) -> list[Hit]:
    tsq = or_query(question)
    if not tsq:
        return []
    where, params = _material_filter(material)
    sql = (_SELECT.replace("AS similarity", "AS similarity, ts_rank_cd(c.tsv, to_tsquery('english', :tsq)) AS r")
           + f" WHERE c.tsv @@ to_tsquery('english', :tsq) {where} ORDER BY r DESC LIMIT :k")
    rows = (await db.execute(text(sql), {"q": qvec, "tsq": tsq, "k": k, **params})).all()
    return [Hit(*r[:7], similarity=float(r[7]), score=float(r[8])) for r in rows]


def rrf(lists: list[list[Hit]], k: int = RRF_K) -> list[Hit]:
    fused: dict[int, Hit] = {}
    scores: dict[int, float] = {}
    for hits in lists:
        for rank, h in enumerate(hits):
            fused.setdefault(h.chunk_id, h)
            scores[h.chunk_id] = scores.get(h.chunk_id, 0.0) + 1.0 / (k + rank + 1)
    out = []
    for cid, h in fused.items():
        h.score = scores[cid]
        out.append(h)
    return sorted(out, key=lambda h: h.score, reverse=True)


async def retrieve(db: AsyncSession, question: str, k: int = 5, mode: str = "hybrid",
                   material: str | None = None) -> list[Hit]:
    qvec = _vec_literal(await asyncio.to_thread(embed_query, question))
    if mode == "vector":
        return await vector_search(db, qvec, k, material)
    if mode == "hybrid":
        vec = await vector_search(db, qvec, CANDIDATES, material)
        kw = await keyword_search(db, question, qvec, CANDIDATES, material)
        return rrf([vec, kw])[:k]
    if mode == "rerank":
        from rag.rerank import RERANK_CANDIDATES, rerank_scores

        vec = await vector_search(db, qvec, CANDIDATES, material)
        kw = await keyword_search(db, question, qvec, CANDIDATES, material)
        cands = rrf([vec, kw])[:RERANK_CANDIDATES]
        scores = await asyncio.to_thread(rerank_scores, question, [h.text for h in cands])
        for h, sc in zip(cands, scores):
            h.score = sc
        return sorted(cands, key=lambda h: h.score, reverse=True)[:k]
    raise ValueError(f"unknown retrieval mode {mode!r}")
