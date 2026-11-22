# Decisions log (made unattended; review in the morning)

1. **Layout.** The SupplyWatch service (`supplywatch/supplywatch/` in the source repo) is copied
   to `app/` keeping its flat imports (`from core.config import ...`). New packages live beside it:
   `app/rag`, `app/agent`, `app/webhooks`, `app/oauth`. Source tests stay in `app/tests`.
2. **Seed materials.** The source seeded 8 materials on startup (incl. Yttrium, Dysprosium, Indium).
   Replaced with the plan's 10 (rare earths grouped as one "Rare Earths" row). Same mechanism.
3. **Synthetic history.** `scripts/seed.py` writes 30 days of *synthetic* 6-hourly scores
   (seeded random walk) so the API has data without calling live yfinance/Google News.
   Tagged `source="synthetic_seed"`, `raw_data.synthetic=true`.
4. **Scheduler off by default** (`ENABLE_SCHEDULER=false`) because the source pipeline calls live
   third-party sources every 6 h.
5. **Source Dockerfile** ran uvicorn on 8050 while exposing 8000; unified on 8000.
6. **No ANTHROPIC_API_KEY on the build server.** The build VPS has a logged-in `claude` CLI but no
   API key. So the LLM layer has backends: `anthropic` (SDK, used when a key is set, e.g. on your
   laptop), `claude_cli` (shells out to `claude -p`, used on the build host for the recorded agent
   run and the evals), `fake` (tests/CI), `none` (extractive fallback, no LLM).
7. **Source bug fixed:** lifespan called `scheduler.shutdown()` even when the scheduler was disabled, crashing app shutdown. Now guarded with `if scheduler.running`.
