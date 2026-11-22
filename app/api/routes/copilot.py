"""Agent + tool endpoints. The MCP server calls the /tools/* endpoints."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from agent import tools_core as core
from agent.graph import run_agent
from api.auth import require_api_key
from core.config import get_settings
from core.database import get_db

router = APIRouter(tags=["copilot"], dependencies=[Depends(require_api_key)])


def _env(data):
    return {"data": data, "meta": {"timestamp": datetime.now(timezone.utc), "version": get_settings().app_version}}


async def _guard(coro):
    try:
        return _env(await coro)
    except core.ToolError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AgentRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    k: int = Field(default=5, ge=1, le=10)
    mode: str = Field(default="hybrid", pattern="^(vector|hybrid|rerank)$")


class ReportRequest(BaseModel):
    material: str
    focus: str | None = None


@router.post("/agent")
async def agent_route(payload: AgentRequest):
    return _env(await run_agent(payload.question))


@router.get("/tools/signal")
async def tool_signal(material: str = Query(...), db: AsyncSession = Depends(get_db)):
    return await _guard(core.get_material_signal(db, material))


@router.get("/tools/history")
async def tool_history(material: str = Query(...), days: int = Query(30, ge=1, le=90),
                       db: AsyncSession = Depends(get_db)):
    return await _guard(core.get_material_history(db, material, days))


@router.get("/tools/supplier-exposure")
async def tool_exposure(company: str | None = None, material: str | None = None, country: str | None = None):
    try:
        return _env(core.supplier_exposure(company, material, country))
    except core.ToolError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/tools/search")
async def tool_search(payload: SearchRequest, db: AsyncSession = Depends(get_db)):
    return _env(await core.search_documents(db, payload.query, payload.k, payload.mode))


@router.post("/tools/risk-report")
async def tool_report(payload: ReportRequest, db: AsyncSession = Depends(get_db)):
    return await _guard(core.write_risk_report(db, payload.material, payload.focus))
