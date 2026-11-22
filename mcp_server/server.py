"""SupplyWatch MCP server (stdio).

Exposes the same tools as the LangGraph agent, by calling the SupplyWatch REST
API, so Claude Desktop (or any MCP client) can use them. Runs anywhere Python
runs; only needs network access to the API.

Config (environment variables):
  SUPPLYWATCH_API_URL        default http://localhost:18000
  SUPPLYWATCH_API_KEY        an X-API-Key from POST /auth/keys       (option A)
  SUPPLYWATCH_CLIENT_ID      OAuth2 client-credentials id            (option B)
  SUPPLYWATCH_CLIENT_SECRET  OAuth2 client-credentials secret

Run:  python mcp_server/server.py
"""
from __future__ import annotations

import json
import os
import time

import httpx
from mcp.server.fastmcp import FastMCP

API_URL = os.environ.get("SUPPLYWATCH_API_URL", "http://localhost:18000").rstrip("/")
mcp = FastMCP("supplywatch")
_token: dict = {"value": None, "exp": 0.0}


def _auth_headers() -> dict:
    key = os.environ.get("SUPPLYWATCH_API_KEY")
    if key:
        return {"X-API-Key": key}
    cid, secret = os.environ.get("SUPPLYWATCH_CLIENT_ID"), os.environ.get("SUPPLYWATCH_CLIENT_SECRET")
    if cid and secret:
        if not _token["value"] or time.time() > _token["exp"] - 30:
            r = httpx.post(f"{API_URL}/oauth/token", timeout=30,
                           data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret,
                                 "scope": "read:materials ask"})
            r.raise_for_status()
            body = r.json()
            _token.update(value=body["access_token"], exp=time.time() + body["expires_in"])
        return {"Authorization": f"Bearer {_token['value']}"}
    raise RuntimeError("Set SUPPLYWATCH_API_KEY, or SUPPLYWATCH_CLIENT_ID + SUPPLYWATCH_CLIENT_SECRET")


def _call(method: str, path: str, **kw) -> str:
    try:
        r = httpx.request(method, f"{API_URL}{path}", headers=_auth_headers(), timeout=180, **kw)
    except httpx.HTTPError as e:
        return json.dumps({"error": f"SupplyWatch API unreachable at {API_URL}: {e}"})
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        return json.dumps({"error": detail, "status": r.status_code})
    return json.dumps(r.json()["data"], default=str)


@mcp.tool()
def list_materials() -> str:
    """List the materials SupplyWatch tracks, with their latest disruption score (0-100)."""
    return _call("GET", "/materials")


@mcp.tool()
def get_material_signal(material: str) -> str:
    """Current SupplyWatch disruption score (0-100), risk band and factor breakdown
    (price momentum, export restrictions, trade concentration) for a material, e.g. 'Lithium'."""
    return _call("GET", "/tools/signal", params={"material": material})


@mcp.tool()
def get_material_history(material: str, days: int = 30) -> str:
    """Disruption-score history for a material over the last `days` days (daily averages, delta)."""
    return _call("GET", "/tools/history", params={"material": material, "days": days})


@mcp.tool()
def supplier_exposure(company: str | None = None, material: str | None = None, country: str | None = None) -> str:
    """Which suppliers (fictional demo directory) source a material, from which countries and at what
    share, and which Acme Drives products depend on them. Filter by company, material and/or country."""
    params = {k: v for k, v in {"company": company, "material": material, "country": country}.items() if v}
    return _call("GET", "/tools/supplier-exposure", params=params)


@mcp.tool()
def search_documents(query: str, k: int = 5) -> str:
    """Search USGS Mineral Commodity Summaries (2024, 2025) and supplier/news documents.
    Returns titles, source URLs and snippets to cite."""
    return _call("POST", "/tools/search", json={"query": query, "k": k})


@mcp.tool()
def write_risk_report(material: str, focus: str | None = None) -> str:
    """Generate a structured JSON supply-risk report for one material: risk level, exposed suppliers,
    key findings, mitigation gaps, recommended next step, confidence and sources."""
    return _call("POST", "/tools/risk-report", json={"material": material, "focus": focus})


if __name__ == "__main__":
    mcp.run()  # stdio
