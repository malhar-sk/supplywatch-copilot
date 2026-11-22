import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

# Copilot additions: tests never call a real LLM and never write the LLM cache.
os.environ.setdefault("LLM_BACKEND", "fake")
os.environ["LLM_CACHE_DIR"] = ""
os.environ.setdefault("DB_NULLPOOL", "1")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:15432/supplywatch")

import asyncio  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _pgvector_extension():
    """CI's Postgres service container doesn't run db/init.sql; make sure pgvector exists
    before any test calls Base.metadata.create_all() (the RAG tables use the vector type)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def _run():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        async with eng.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await eng.dispose()

    asyncio.run(_run())
