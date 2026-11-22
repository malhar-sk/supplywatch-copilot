"""Shared helpers for the copilot tests (DB-backed, LLM mocked)."""
import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parents[2]
CORPUS = Path(__import__("os").environ.get("CORPUS_DIR", REPO / "data" / "corpus"))

# A small, fixed slice of the real corpus used by retrieval tests.
SUBSET = [
    "usgs/usgs-2025-gallium-events-trends-and-issues.md",
    "usgs/usgs-2025-antimony-substitutes.md",
    "usgs/usgs-2025-lithium-world-production-and-reserves.md",
    "usgs/usgs-2025-cobalt-domestic-production-and-use.md",
    "usgs/usgs-2025-graphite-events-trends-and-issues.md",
    "usgs/usgs-2025-rare-earths-world-resources.md",
    "synthetic/supplier-kivu-cobalt-logistics.md",
    "synthetic/customer-acme-drives-bom.md",
]


def ingest_subset():
    from core.database import engine
    from rag.ingest import ingest_documents, parse_doc

    async def _run():
        await engine.dispose()
        docs = [parse_doc((CORPUS / p).read_text(encoding="utf-8")) for p in SUBSET]
        out = await ingest_documents(docs)
        await engine.dispose()
        return out

    return asyncio.run(_run())


def make_client() -> TestClient:
    from core.database import engine
    from main import app

    asyncio.run(engine.dispose())
    return TestClient(app)


def new_api_key(client: TestClient, tier: str = "pro") -> str:
    r = client.post("/auth/keys", json={"company_name": "TestCo", "tier": tier})
    assert r.status_code == 200
    return r.json()["data"]["api_key"]
