"""M3: LangGraph agent with a mocked LLM routes to the right tool and returns a trace."""
import asyncio
import pytest
from langchain_core.messages import AIMessage

from agent.chat_models import KeywordRouterModel, ScriptedChatModel, parse_protocol
from agent.graph import TOOLS, run_agent
from tests.helpers import ingest_subset, make_client, new_api_key, seed_db


@pytest.fixture(scope="module", autouse=True)
def seeded():
    seed_db()
    ingest_subset()


def call(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"c-{name}", "type": "tool_call"}])


def test_graph_exposes_five_tools():
    assert {t.name for t in TOOLS} == {"get_material_signal", "get_material_history", "supplier_exposure",
                                       "search_documents", "write_risk_report"}


@pytest.mark.parametrize("tool,args,expect_key", [
    ("get_material_signal", {"material": "Lithium"}, "score"),
    ("get_material_history", {"material": "Gallium", "days": 7}, "daily"),
    ("supplier_exposure", {"material": "Cobalt", "country": "DRC"}, "matches"),
    ("search_documents", {"query": "gallium export ban"}, "results"),
    ("write_risk_report", {"material": "Antimony"}, "report"),
])
def test_graph_routes_scripted_tool_call_and_finishes(tool, args, expect_key):
    model = ScriptedChatModel(responses=[call(tool, args), AIMessage(content="final answer from tools")])
    out = asyncio.run(run_agent("question", model=model))
    assert out["answer"] == "final answer from tools"
    assert [t["tool"] for t in out["trace"]] == [tool]
    assert out["trace"][0]["args"] == args
    preview = out["trace"][0]["result_preview"] or ""
    assert expect_key in preview
    assert '"error"' not in preview[:20]
    # the model saw the tool result before writing its final answer
    assert any(m.type == "tool" for m in model.seen[-1])


def test_graph_multi_step_trace_order():
    model = ScriptedChatModel(responses=[
        call("get_material_signal", {"material": "Gallium"}),
        call("supplier_exposure", {"material": "Gallium"}),
        AIMessage(content="done"),
    ])
    out = asyncio.run(run_agent("Gallium risk and who is exposed?", model=model))
    assert [(t["step"], t["tool"]) for t in out["trace"]] == [(1, "get_material_signal"), (2, "supplier_exposure")]


def test_unknown_material_is_reported_as_tool_error_not_crash():
    model = ScriptedChatModel(responses=[call("get_material_signal", {"material": "Unobtainium"}),
                                         AIMessage(content="no such material")])
    out = asyncio.run(run_agent("Unobtainium?", model=model))
    assert "unknown material" in out["trace"][0]["result_preview"]


def test_risk_report_fallback_matches_schema():
    from agent.tools_core import RiskReport, write_risk_report
    from core.database import AsyncSessionLocal

    async def _run():
        async with AsyncSessionLocal() as db:
            return await write_risk_report(db, "Gallium")

    out = asyncio.run(_run())
    rep = RiskReport.model_validate(out["report"])  # FakeLLM output is not JSON -> schema-valid fallback
    assert rep.material == "Gallium" and rep.overallRiskLevel in {"High", "Medium", "Low"}
    assert any(s.name == "Lumen Semiconductor Substrates" for s in rep.exposedSuppliers)


@pytest.mark.parametrize("q,tool", [
    ("What is the current risk score for lithium?", "get_material_signal"),
    ("Show the gallium trend over the last 30 days", "get_material_history"),
    ("Which suppliers are exposed to cobalt?", "supplier_exposure"),
    ("Write a risk report for antimony", "write_risk_report"),
    ("Why did China restrict graphite exports?", "search_documents"),
])
def test_keyword_router(q, tool):
    assert KeywordRouterModel.route(q)[0] == tool


def test_cli_json_protocol_parsing():
    m = parse_protocol('```json\n{"tool_calls": [{"name": "get_material_signal", "args": {"material": "Nickel"}}]}\n```')
    assert m.tool_calls[0]["name"] == "get_material_signal" and m.tool_calls[0]["args"] == {"material": "Nickel"}
    assert parse_protocol('{"final": "Nickel is low risk."}').content == "Nickel is low risk."
    assert parse_protocol("plain text").content == "plain text"


def test_agent_endpoint_returns_answer_and_trace():
    with make_client() as client:
        key = new_api_key(client)
        r = client.post("/agent", headers={"X-API-Key": key}, json={"question": "What is the current risk score for gallium?"})
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["trace"][0]["tool"] == "get_material_signal"
        assert d["answer"]


def test_tool_rest_endpoints():
    with make_client() as client:
        h = {"X-API-Key": new_api_key(client)}
        assert client.get("/tools/signal", params={"material": "lithium"}, headers=h).json()["data"]["material"] == "Lithium"
        assert client.get("/tools/signal", params={"material": "unobtainium"}, headers=h).status_code == 404
        hist = client.get("/tools/history", params={"material": "Cobalt", "days": 10}, headers=h).json()["data"]
        assert hist["n_readings"] >= 30
        exp = client.get("/tools/supplier-exposure", params={"company": "Kivu"}, headers=h).json()["data"]
        assert exp["matches"][0]["company"] == "Kivu Cobalt Logistics"
        s = client.post("/tools/search", json={"query": "antimony substitutes"}, headers=h).json()["data"]
        assert s["results"]
