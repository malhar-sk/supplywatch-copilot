"""M4: a real MCP client spawns the stdio server, lists tools and calls get_material_signal
against a live (test) SupplyWatch API started in a subprocess."""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def api_url():
    sys.path.insert(0, str(ROOT / "app"))
    from tests.helpers import seed_db  # app/tests/helpers.py

    seed_db()
    port = _free_port()
    env = {**os.environ, "LLM_BACKEND": "fake", "ENABLE_SCHEDULER": "false", "LLM_CACHE_DIR": ""}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--port", str(port)],
                            cwd=ROOT / "app", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail("API did not start")
    yield url
    proc.terminate()
    proc.wait(10)


def test_mcp_client_lists_tools_and_calls_signal(api_url):
    key = httpx.post(f"{api_url}/auth/keys", json={"company_name": "MCP test", "tier": "pro"}).json()["data"]["api_key"]
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")],
                                   env={**os.environ, "SUPPLYWATCH_API_URL": api_url, "SUPPLYWATCH_API_KEY": key})

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                res = await session.call_tool("get_material_signal", {"material": "Lithium"})
                bad = await session.call_tool("get_material_signal", {"material": "Unobtainium"})
                return tools, res, bad

    tools, res, bad = asyncio.run(_run())
    assert {"get_material_signal", "get_material_history", "supplier_exposure", "search_documents",
            "write_risk_report", "list_materials"} <= tools
    assert not res.isError
    data = json.loads(res.content[0].text)
    assert data["material"] == "Lithium" and 0 <= data["score"] <= 100
    assert "unknown material" in json.loads(bad.content[0].text)["error"]


def test_mcp_server_with_oauth_client_credentials(api_url):
    key = httpx.post(f"{api_url}/auth/keys", json={"company_name": "MCP oauth", "tier": "pro"}).json()["data"]["api_key"]
    cred = httpx.post(f"{api_url}/oauth/clients", headers={"X-API-Key": key},
                      json={"name": "claude-desktop", "scopes": ["read:materials", "ask"]}).json()["data"]
    env = {k: v for k, v in os.environ.items() if k != "SUPPLYWATCH_API_KEY"}
    env.update(SUPPLYWATCH_API_URL=api_url, SUPPLYWATCH_CLIENT_ID=cred["client_id"],
               SUPPLYWATCH_CLIENT_SECRET=cred["client_secret"])
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server" / "server.py")], env=env)

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                mats = await session.call_tool("list_materials", {})
                exp = await session.call_tool("supplier_exposure", {"material": "Gallium", "country": "China"})
                return mats, exp

    mats, exp = asyncio.run(_run())
    names = {m["name"] for m in json.loads(mats.content[0].text)}
    assert {"Lithium", "Gallium", "Rare Earths"} <= names
    assert json.loads(exp.content[0].text)["matches"][0]["company"] == "Lumen Semiconductor Substrates"
