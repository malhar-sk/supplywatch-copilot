"""LangGraph analyst agent:  START -> agent --(tool_calls?)--> tools -> agent ... -> END."""
from __future__ import annotations

import json
import time

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent import tools_core as core
from agent.chat_models import get_agent_chat_model
from core.database import AsyncSessionLocal

SYSTEM_PROMPT = """You are SupplyWatch Analyst, an agent for critical-mineral supply risk.
Use tools to ground every claim:
- get_material_signal: current SupplyWatch disruption score (0-100) and factor breakdown for a material
- get_material_history: last N days of scores for a material (trend questions)
- supplier_exposure: which (fictional demo) suppliers source a material / from which countries
- search_documents: search USGS Mineral Commodity Summaries + supplier/news documents
- write_risk_report: structured JSON risk report for one material (use when asked for a report)
Prefer the fewest tool calls that answer the question. Name the tools' sources in your answer.
Supplier data is fictional demo data; say so when you use it. Answer in at most 6 sentences."""


def _err(e: Exception) -> str:
    return json.dumps({"error": str(e)})


@tool
async def get_material_signal(material: str) -> str:
    """Current SupplyWatch disruption score (0-100), risk band and factor breakdown for a material
    (e.g. 'Lithium', 'Gallium', 'Rare Earths')."""
    async with AsyncSessionLocal() as db:
        try:
            return json.dumps(await core.get_material_signal(db, material), default=str)
        except core.ToolError as e:
            return _err(e)


@tool
async def get_material_history(material: str, days: int = 30) -> str:
    """Score history for a material over the last `days` days (daily averages, first/last/delta)."""
    async with AsyncSessionLocal() as db:
        try:
            return json.dumps(await core.get_material_history(db, material, days), default=str)
        except core.ToolError as e:
            return _err(e)


@tool
async def supplier_exposure(company: str | None = None, material: str | None = None,
                            country: str | None = None) -> str:
    """Look up the fictional demo supplier directory: which suppliers source a material, from which
    countries and at what share, and which Acme Drives products depend on them. Give a company name,
    a material, a country, or a combination."""
    try:
        return json.dumps(core.supplier_exposure(company, material, country))
    except core.ToolError as e:
        return _err(e)


@tool
async def search_documents(query: str, k: int = 5) -> str:
    """Hybrid (vector + keyword) search over USGS Mineral Commodity Summaries 2024/2025 sections and
    supplier/news documents. Returns titles, sources and snippets."""
    async with AsyncSessionLocal() as db:
        return json.dumps(await core.search_documents(db, query, k=min(max(k, 1), 8)), default=str)


@tool
async def write_risk_report(material: str, focus: str | None = None) -> str:
    """Write a structured JSON supply-risk report for one material (score, exposed suppliers,
    findings, gaps, next step, confidence, sources)."""
    async with AsyncSessionLocal() as db:
        try:
            return json.dumps(await core.write_risk_report(db, material, focus), default=str)
        except core.ToolError as e:
            return _err(e)


TOOLS = [get_material_signal, get_material_history, supplier_exposure, search_documents, write_risk_report]


def build_graph(model: BaseChatModel):
    bound = model.bind_tools(TOOLS)

    async def agent_node(state: MessagesState):
        return {"messages": [await bound.ainvoke(state["messages"])]}

    g = StateGraph(MessagesState)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(TOOLS))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


def extract_trace(messages) -> list[dict]:
    trace = []
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    step = 0
    for m in messages:
        if isinstance(m, AIMessage) and m.tool_calls:
            step += 1
            for tc in m.tool_calls:
                r = results.get(tc["id"])
                out = str(r.content) if r else None
                trace.append({"step": step, "tool": tc["name"], "args": tc["args"],
                              "result_preview": (out[:600] + "...") if out and len(out) > 600 else out})
    return trace


async def run_agent(question: str, model: BaseChatModel | None = None, max_steps: int = 6) -> dict:
    model = model or get_agent_chat_model()
    graph = build_graph(model)
    t0 = time.perf_counter()
    state = await graph.ainvoke(
        {"messages": [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=question)]},
        config={"recursion_limit": 2 * max_steps + 1})
    msgs = state["messages"]
    final = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and not m.tool_calls), None)
    usage = {"input_tokens": 0, "output_tokens": 0}
    for m in msgs:
        if isinstance(m, AIMessage) and m.usage_metadata:
            usage["input_tokens"] += m.usage_metadata.get("input_tokens", 0)
            usage["output_tokens"] += m.usage_metadata.get("output_tokens", 0)
    content = final.content if final else ""
    if isinstance(content, list):  # anthropic content blocks
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return {"question": question, "answer": content, "trace": extract_trace(msgs),
            "model": getattr(model, "model", None) or getattr(model, "model_name", None) or model._llm_type,
            "usage": usage, "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
