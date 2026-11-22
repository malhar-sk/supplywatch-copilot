from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_scope
from core.config import get_settings
from core.database import Material, get_db
from rag.answer import ask

router = APIRouter(tags=["copilot"])


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    material_id: int | None = None
    mode: str | None = Field(default=None, pattern="^(vector|hybrid|rerank)$")
    include_contexts: bool = False  # return full retrieved chunk texts (used by the evals)


@router.post("/ask", dependencies=[Depends(require_scope("ask"))])
async def ask_route(payload: AskRequest, db: AsyncSession = Depends(get_db)):
    material = None
    if payload.material_id is not None:
        m = (await db.execute(select(Material).where(Material.id == payload.material_id))).scalar_one_or_none()
        if not m:
            raise HTTPException(status_code=404, detail="material not found")
        material = m.name
    r = await ask(db, payload.question, material=material, mode=payload.mode)
    return {"data": {"answer": r.answer, "refused": r.refused, "citations": r.citations,
                     "retrieval_mode": r.retrieval_mode, "top_similarity": r.top_similarity,
                     "usage": r.usage, "timings_ms": r.timings_ms, "material": material,
                     **({"contexts": r.contexts, "retrieved_doc_ids": r.retrieved_doc_ids}
                        if payload.include_contexts else {})},
            "meta": {"timestamp": datetime.now(timezone.utc), "version": get_settings().app_version}}
