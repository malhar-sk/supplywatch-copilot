"""Tool implementations shared by the LangGraph agent, the REST API and (via
REST) the MCP server. Plain async functions returning JSON-serialisable dicts."""
from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import DisruptionScore, Material
from core.llm import get_text_llm
from rag.retrieval import retrieve

DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[2] / "data"))
ALIASES = {"rare earth": "Rare Earths", "ree": "Rare Earths", "rare earth elements": "Rare Earths",
           "li": "Lithium", "co": "Cobalt", "ni": "Nickel", "ga": "Gallium", "ge": "Germanium",
           "w": "Tungsten", "sb": "Antimony", "mn": "Manganese"}


class ToolError(Exception):
    pass


def band(score: int) -> str:
    # Same bands as the source's snapshot.summarize()
    return "elevated" if score >= 70 else "moderate" if score >= 40 else "low"


async def resolve_material(db: AsyncSession, material: str | int) -> Material:
    if isinstance(material, int) or str(material).isdigit():
        m = await db.get(Material, int(material))
    else:
        name = ALIASES.get(str(material).strip().lower(), str(material).strip())
        m = (await db.execute(select(Material).where(func.lower(Material.name) == name.lower()))).scalar_one_or_none()
        if not m:  # tolerate plural/singular ("rare earth", "lithium ")
            m = (await db.execute(select(Material).where(Material.name.ilike(f"%{name.rstrip('s')}%")))).scalars().first()
    if not m:
        names = (await db.execute(select(Material.name))).scalars().all()
        raise ToolError(f"unknown material {material!r}; known: {', '.join(sorted(names))}")
    return m


async def get_material_signal(db: AsyncSession, material: str | int) -> dict:
    m = await resolve_material(db, material)
    s = (await db.execute(select(DisruptionScore).where(DisruptionScore.material_id == m.id)
                          .order_by(desc(DisruptionScore.computed_at)).limit(1))).scalar_one_or_none()
    if not s:
        raise ToolError(f"no signal recorded for {m.name}")
    f = s.factors or {}
    dominant = max(f, key=f.get) if f else None
    return {"material_id": m.id, "material": m.name, "score": s.score, "band": band(s.score),
            "factors": f, "dominant_factor": dominant, "as_of": s.computed_at.isoformat(),
            "primary_producers": m.primary_producers}


async def get_material_history(db: AsyncSession, material: str | int, days: int = 30) -> dict:
    m = await resolve_material(db, material)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (await db.execute(select(DisruptionScore).where(DisruptionScore.material_id == m.id,
                                                           DisruptionScore.computed_at >= cutoff)
                             .order_by(DisruptionScore.computed_at))).scalars().all()
    if not rows:
        raise ToolError(f"no history for {m.name} in the last {days} days")
    daily: dict[str, list[int]] = {}
    for r in rows:
        daily.setdefault(r.computed_at.date().isoformat(), []).append(r.score)
    points = [{"date": d, "score": round(sum(v) / len(v), 1)} for d, v in daily.items()]
    scores = [r.score for r in rows]
    return {"material_id": m.id, "material": m.name, "days": days, "n_readings": len(rows),
            "first": scores[0], "last": scores[-1], "delta": scores[-1] - scores[0],
            "min": min(scores), "max": max(scores), "daily": points}


@lru_cache(maxsize=1)
def _supplier_data() -> dict:
    return json.loads((DATA_DIR / "suppliers.json").read_text(encoding="utf-8"))


def supplier_exposure(company: str | None = None, material: str | None = None, country: str | None = None) -> dict:
    """Look up the (FICTIONAL) supplier directory by company and/or material and/or country."""
    if not (company or material or country):
        raise ToolError("give at least one of company, material, country")
    data = _supplier_data()
    mat = ALIASES.get((material or "").lower(), material)
    out = []
    for s in data["suppliers"]:
        if company and company.lower() not in s["company"].lower():
            continue
        rows = [x for x in s["materials"]
                if (not mat or mat.lower().rstrip("s") in x["material"].lower())
                and (not country or country.lower() == x["country"].lower())]
        if not rows:
            continue
        used_in = [p["product"] for p in data["customer"]["products"] if s["company"] in p["suppliers"]]
        out.append({"company": s["company"], "hq": s["hq"], "tier": s["tier"], "sourcing": rows,
                    "acme_products": used_in})
    return {"query": {"company": company, "material": material, "country": country}, "synthetic": True,
            "note": "Supplier directory is FICTIONAL demo data.", "matches": out}


async def search_documents(db: AsyncSession, query: str, k: int = 5, mode: str = "hybrid",
                           material: str | None = None) -> dict:
    hits = await retrieve(db, query, k=k, mode=mode, material=material)
    return {"query": query, "results": [h.as_citation(i, snippet_chars=600) for i, h in enumerate(hits, 1)]}


# ---------------- write_risk_report (schema adapted from pharmawatch) ----------------

class ExposedSupplier(BaseModel):
    name: str
    country: str
    sharePct: float | None = None
    exposureReason: str


class RiskReport(BaseModel):
    material: str
    analysisDate: str
    overallRiskLevel: Literal["High", "Medium", "Low", "Unknown"]
    riskScore: int | None = Field(default=None, ge=0, le=100)
    riskRationale: str
    exposedSuppliers: list[ExposedSupplier] = []
    keyFindings: str
    mitigationGaps: list[str] = []
    recommendedNextStep: str
    dataConfidence: Literal["High", "Medium", "Low"]
    dataConfidenceReason: str
    citedSources: list[str] = []
    disclaimer: str = ("Generated by AI from SupplyWatch signals, USGS public data and FICTIONAL demo supplier "
                       "data. Verify before making sourcing decisions.")


REPORT_SYSTEM = """You are a critical-mineral supply-chain risk analyst (adapted from the PharmaWatch BIOSECURE analyst).
Using ONLY the evidence provided, return ONLY a valid JSON object (no markdown) with this exact schema:
{
  "material": "string",
  "analysisDate": "ISO date string",
  "overallRiskLevel": "High|Medium|Low|Unknown",
  "riskScore": "integer 0-100 (copy the SupplyWatch score)",
  "riskRationale": "1-2 sentences explaining the risk level",
  "exposedSuppliers": [{"name": "string", "country": "string", "sharePct": number, "exposureReason": "string"}],
  "keyFindings": "2-3 sentences summarizing the most critical findings",
  "mitigationGaps": ["string"],
  "recommendedNextStep": "single most important action",
  "dataConfidence": "High|Medium|Low",
  "dataConfidenceReason": "string",
  "citedSources": ["document titles you relied on"],
  "disclaimer": "string"
}
Risk level rules: score >= 70 -> High, 40-69 -> Medium, < 40 -> Low, unless the documents give strong reason to differ (say why).
Confidence rules: High = SupplyWatch score + official (USGS) documents agree; Medium = partial evidence; Low = mostly inferred."""


def _level(score: int | None) -> str:
    if score is None:
        return "Unknown"
    return "High" if score >= 70 else "Medium" if score >= 40 else "Low"


def _fallback_report(material: str, signal: dict | None, exposure: dict, docs: dict) -> RiskReport:
    score = signal["score"] if signal else None
    exposed = [ExposedSupplier(name=m["company"], country=x["country"], sharePct=x["share_pct"],
                               exposureReason=f"{x['share_pct']}% of its {x['material']} from {x['country']}")
               for m in exposure["matches"] for x in m["sourcing"]]
    return RiskReport(
        material=material, analysisDate=date.today().isoformat(), overallRiskLevel=_level(score), riskScore=score,
        riskRationale=f"SupplyWatch score {score}/100 ({band(score) if score is not None else 'n/a'} band).",
        exposedSuppliers=exposed[:10],
        keyFindings=" ".join(r["snippet"][:200] for r in docs["results"][:2]) or "No supporting documents found.",
        mitigationGaps=["Deterministic fallback report (no LLM configured): gaps not analysed."],
        recommendedNextStep="Review the exposed suppliers' sourcing countries against current export controls.",
        dataConfidence="Low", dataConfidenceReason="Template report built without an LLM.",
        citedSources=[r["title"] for r in docs["results"]])


async def write_risk_report(db: AsyncSession, material: str, focus: str | None = None,
                            backend: str | None = None) -> dict:
    m = await resolve_material(db, material)
    try:
        signal = await get_material_signal(db, m.id)
    except ToolError:
        signal = None
    try:
        history = await get_material_history(db, m.id, days=30)
        history = {k: history[k] for k in ("first", "last", "delta", "min", "max", "n_readings")}
    except ToolError:
        history = None
    exposure = supplier_exposure(material=m.name)
    docs = await search_documents(db, f"{m.name} supply risk export restrictions {focus or ''}".strip(), k=5,
                                  material=m.name)
    llm = get_text_llm(backend=backend)
    if llm is None:
        return {"report": _fallback_report(m.name, signal, exposure, docs).model_dump(), "generated_by": "fallback"}
    evidence = json.dumps({"supplywatch_signal": signal, "history_30d": history, "supplier_exposure": exposure,
                           "documents": [{"title": r["title"], "text": r["snippet"]} for r in docs["results"]],
                           "focus": focus, "today": date.today().isoformat()}, default=str)
    res = await asyncio.to_thread(llm.complete, REPORT_SYSTEM, f"Material: {m.name}\nEvidence:\n{evidence}", 1500)
    txt = res.text
    try:
        report = RiskReport.model_validate_json(txt[txt.index("{"): txt.rindex("}") + 1])
        return {"report": report.model_dump(), "generated_by": res.model,
                "usage": {"input_tokens": res.input_tokens, "output_tokens": res.output_tokens}}
    except (ValueError, ValidationError) as exc:
        fb = _fallback_report(m.name, signal, exposure, docs).model_dump()
        return {"report": fb, "generated_by": "fallback", "llm_error": f"invalid report JSON: {str(exc)[:200]}"}
