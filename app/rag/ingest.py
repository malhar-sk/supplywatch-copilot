"""Ingest data/corpus/**/*.md into rag_documents + rag_chunks (idempotent)."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from sqlalchemy import delete, text

from core.database import AsyncSessionLocal, Base, engine
from rag.chunking import chunk_text
from rag.embeddings import embed_passages, tokenizer
from rag.models import Chunk, Document

DEFAULT_CORPUS = Path(os.environ.get("CORPUS_DIR", Path(__file__).resolve().parents[2] / "data" / "corpus"))
META_KEYS = {"id", "title", "source", "source_url", "fetched_at", "material", "doc_type", "synthetic"}


def parse_doc(raw: str) -> tuple[dict, str]:
    head, _, body = raw.partition("\n---\n")
    meta: dict = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        v = v.strip()
        if v in ("true", "false") or v.startswith("["):
            v = json.loads(v)
        meta[k.strip()] = v
    if "id" not in meta or "title" not in meta:
        raise ValueError("document header needs at least id and title")
    return meta, body.strip()


def count_tokens(s: str) -> int:
    return len(tokenizer().encode(s, add_special_tokens=False))


def prepare(meta: dict, body: str) -> list[tuple[str, int]]:
    # Prefix the title so every chunk carries its document context.
    return [(f"{meta['title']}\n{c}", n) for c, n in chunk_text(body, count_tokens)]


async def ensure_schema() -> None:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)


async def ingest_documents(docs: list[tuple[dict, str]]) -> dict:
    await ensure_schema()
    n_chunks = 0
    async with AsyncSessionLocal() as db:
        for meta, body in docs:
            chunks = prepare(meta, body)
            vecs = await asyncio.to_thread(embed_passages, [c for c, _ in chunks])
            await db.execute(delete(Document).where(Document.id == meta["id"]))
            await db.flush()
            db.add(Document(
                id=meta["id"], title=meta["title"], source=meta.get("source", ""),
                source_url=meta.get("source_url", ""), fetched_at=str(meta.get("fetched_at", "")),
                material=meta.get("material", ""), doc_type=meta.get("doc_type", ""),
                synthetic=bool(meta.get("synthetic", False)),
                meta={k: v for k, v in meta.items() if k not in META_KEYS}))
            await db.flush()
            for i, ((c, n), v) in enumerate(zip(chunks, vecs)):
                db.add(Chunk(document_id=meta["id"], chunk_index=i, text=c, n_tokens=n, embedding=v))
            n_chunks += len(chunks)
        await db.commit()
    return {"documents": len(docs), "chunks": n_chunks}


async def ingest_corpus_dir(corpus_dir: Path | str | None = None) -> dict:
    root = Path(corpus_dir or DEFAULT_CORPUS)
    files = sorted(root.rglob("*.md"))
    docs = [parse_doc(f.read_text(encoding="utf-8")) for f in files]
    return await ingest_documents(docs)


if __name__ == "__main__":
    print(asyncio.run(ingest_corpus_dir()))
