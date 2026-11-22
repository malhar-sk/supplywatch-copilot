"""Record one live agent run to examples/agent_run.json (host; needs an LLM backend).

    DATABASE_URL=... LLM_BACKEND=claude_cli python scripts/record_agent_run.py "question"
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from agent.graph import run_agent  # noqa: E402
from core.config import get_settings  # noqa: E402
from core.llm import resolve_backend  # noqa: E402

q = sys.argv[1] if len(sys.argv) > 1 else (
    "Our EV inverter line depends on gallium. How risky is gallium right now, how has the score moved this "
    "month, which of our suppliers are exposed, and what does USGS say about China's export controls?")
out = asyncio.run(run_agent(q))
out["recorded_at"] = datetime.now(timezone.utc).isoformat()
out["backend"] = resolve_backend()
out["agent_model"] = get_settings().agent_model
out["note"] = "Live run. Score history is SYNTHETIC seed data; supplier data is FICTIONAL."
dest = ROOT / "examples" / (sys.argv[2] if len(sys.argv) > 2 else "agent_run.json")
dest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
print(json.dumps({"tools": [t["tool"] for t in out["trace"]], "latency_ms": out["latency_ms"], "usage": out["usage"]}))
print(out["answer"])
