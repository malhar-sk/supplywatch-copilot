"""M2: chunking, ingest, retrieval (known question -> known doc in top 5), /ask citations + refusal."""
import asyncio

import pytest

from rag.chunking import chunk_text
from rag.ingest import parse_doc
from tests.helpers import ingest_subset, make_client, new_api_key


def _words(s):
    return len(s.split())


def test_chunking_respects_size_and_overlap():
    text = " ".join(f"Sentence number {i} talks about gallium export controls." for i in range(200))
    chunks = chunk_text(text, _words, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 5
    assert all(n <= 100 for _, n in chunks)
    # the last sentences of chunk i reappear at the start of chunk i+1
    first_tail = chunks[0][0].split(". ")[-1]
    assert first_tail.rstrip(".") in chunks[1][0]


def test_parse_doc_header():
    meta, body = parse_doc("id: x-1\ntitle: T\nsynthetic: true\nsource_url: http://a\n---\nHello body.")
    assert meta == {"id": "x-1", "title": "T", "synthetic": True, "source_url": "http://a"}
    assert body == "Hello body."
    with pytest.raises(ValueError):
        parse_doc("title: no id\n---\nbody")


@pytest.fixture(scope="module")
def ingested():
    return ingest_subset()


def test_ingest_writes_documents_and_chunks(ingested):
    assert ingested["documents"] == 8
    assert ingested["chunks"] >= 8

    from core.database import AsyncSessionLocal, engine
    from sqlalchemy import text

    async def _count():
        await engine.dispose()
        async with AsyncSessionLocal() as db:
            n = (await db.execute(text(
                "SELECT count(*) FROM rag_chunks WHERE document_id = 'usgs-2025-gallium-events-trends-and-issues' "
                "AND embedding IS NOT NULL"))).scalar_one()
        await engine.dispose()
        return n

    assert asyncio.run(_count()) >= 1


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
@pytest.mark.parametrize("question,doc_id", [
    ("When did China ban gallium exports to the United States?", "usgs-2025-gallium-events-trends-and-issues"),
    # 2024 and 2025 editions of this section are word-for-word identical: either is correct
    ("What can replace antimony in flame retardants?", ("usgs-2025-antimony-substitutes", "usgs-2024-antimony-substitutes")),
    ("What share of Kivu Cobalt Logistics' cobalt comes from the DRC?", "supplier-kivu-cobalt-logistics"),
])
def test_known_question_retrieves_known_doc_top5(ingested, mode, question, doc_id):
    from core.database import AsyncSessionLocal, engine
    from rag.retrieval import retrieve

    async def _run():
        await engine.dispose()
        async with AsyncSessionLocal() as db:
            hits = await retrieve(db, question, k=5, mode=mode)
        await engine.dispose()
        return hits

    hits = asyncio.run(_run())
    accepted = {doc_id} if isinstance(doc_id, str) else set(doc_id)
    assert accepted & {h.document_id for h in hits}


def test_ask_returns_answer_with_citations(ingested):
    with make_client() as client:
        key = new_api_key(client)
        r = client.post("/ask", headers={"X-API-Key": key},
                        json={"question": "When did China ban exports of gallium to the United States?"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["refused"] is False
        assert d["citations"], "expected at least one citation"
        c = d["citations"][0]
        assert {"title", "source", "snippet", "document_id", "source_url"} <= set(c)
        assert any("gallium" in x["document_id"] for x in d["citations"])


def test_ask_refuses_off_topic(ingested):
    with make_client() as client:
        key = new_api_key(client)
        r = client.post("/ask", headers={"X-API-Key": key},
                        json={"question": "What is the best recipe for chocolate cake?"})
        d = r.json()["data"]
        assert d["refused"] is True
        assert d["citations"] == []
        assert "don't know" in d["answer"]


def test_ask_requires_auth():
    with make_client() as client:
        assert client.post("/ask", json={"question": "anything at all"}).status_code == 401
