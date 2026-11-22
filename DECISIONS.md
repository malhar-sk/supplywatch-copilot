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
8. **Alerts are edge-triggered.** The source re-sent an unsigned webhook on every 6-hourly run
   while score >= threshold. Now `webhooks/alerts.py` fires once when a score crosses a threshold
   upward (previous < threshold <= new), signs it (HMAC-SHA256 over `"<ts>.<raw body>"`), retries
   on network errors/408/425/429/5xx with exponential backoff, and logs every attempt in
   `webhook_deliveries`. One shared signing secret (`WEBHOOK_SIGNING_SECRET`) rather than one per
   subscription, so the n8n workflow can verify with a single env var.
9. **n8n pinned to 1.123.99** (stable 1.x line). The workflow is generated from
   `n8n/build_workflow.py`, imported and activated automatically on container start. Verification
   and CSV append use Code nodes (`NODE_FUNCTION_ALLOW_BUILTIN=crypto,fs`) because HMAC needs the raw body.
10. **Telegram token empty on the build host**, so the workflow takes the "log instead" branch and
    writes `n8n_data/telegram_mock.log`. Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` to send for real.
11. **Retrieval "improvement" chosen after looking at golden-set retrieval numbers.** I tried
    hybrid RRF (no gain), cross-encoder rerank over 40 candidates (good but ~5.7 s/query) and over
    the top 10 (kept). That is selection on the test set with 3 candidates; there is no separate dev set.
