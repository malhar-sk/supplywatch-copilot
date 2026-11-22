# TESTING: run and check it yourself

Written for a **Windows laptop with Docker Desktop, Git and Python 3.11+**. Commands are for
**PowerShell**. A Git Bash / macOS / Linux version follows where it differs. Do the steps in order.
Every step says what you should see.

Ports used on your machine (change them in `.env` if they're taken): API **18000**, Postgres
**15432**, n8n **15678**.

---

## 0. Clone

```powershell
git clone https://github.com/malhar-sk/supplywatch-copilot.git
cd supplywatch-copilot
```

## 1. Configure, start, seed

```powershell
copy .env.example .env
notepad .env        # optional: paste ANTHROPIC_API_KEY=sk-ant-...  (everything else can stay as is)
docker compose up -d --build
```

The first build takes about 5–10 minutes: it downloads CPU PyTorch and bakes the two small models
(bge-small embeddings and the MiniLM reranker) into the image. When it finishes:

```powershell
docker compose ps
```
You should see `db`, `api` and `n8n`, all `running (healthy)` (the api turns healthy about 20 s after start).

```powershell
docker compose exec api python scripts/seed.py
```
You should see:
```
seeded 10 materials, 1200 synthetic score rows
ingested corpus: {'documents': 203, 'chunks': 210, 'pruned': 0}
```
(Ingesting embeds 203 documents on the CPU and takes about a minute.)

```powershell
curl.exe http://localhost:18000/health
```
You should see `{"data":{"status":"ok"},...}`. Interactive API docs: http://localhost:18000/docs

> **Without `ANTHROPIC_API_KEY`** everything still works, but `/ask` returns an *extractive* answer
> (the best-matching sentences, labelled `(extractive answer: no LLM configured)`) and `/agent`
> uses a keyword router instead of Claude. With the key, `/ask` uses Claude Haiku and `/agent` Claude Sonnet.

## 2. Ask a question and see citations

```powershell
$key = (Invoke-RestMethod -Method Post -Uri http://localhost:18000/auth/keys -ContentType 'application/json' -Body '{"company_name":"Me","tier":"pro"}').data.api_key
$r = Invoke-RestMethod -Method Post -Uri http://localhost:18000/ask -Headers @{"X-API-Key"=$key} -ContentType 'application/json' -Body '{"question":"When did China ban gallium exports to the United States?"}'
$r.data.answer
$r.data.citations | Select-Object n, title, source_url
```
You should see an answer mentioning **December 2024**, with citations such as
`USGS MCS 2025 - Gallium - Events Trends And Issues` and the `pubs.usgs.gov` PDF URL.

Now a question the corpus can't answer:
```powershell
(Invoke-RestMethod -Method Post -Uri http://localhost:18000/ask -Headers @{"X-API-Key"=$key} -ContentType 'application/json' -Body '{"question":"What is the best recipe for chocolate cake?"}').data
```
You should see `refused : True`, an answer starting `I don't know based on the available documents.`,
and an empty `citations`.

The agent, which calls tools and returns its trace:
```powershell
$a = Invoke-RestMethod -Method Post -Uri http://localhost:18000/agent -Headers @{"X-API-Key"=$key} -ContentType 'application/json' -Body '{"question":"How risky is gallium right now and which of our suppliers are exposed?"}'
$a.data.answer; $a.data.trace | Select-Object step, tool, args
```
You should see the tools used (with a key: usually `get_material_signal`, `supplier_exposure`,
`search_documents`). A recorded live run is in `examples/agent_run.json`.

<details><summary>Git Bash / macOS / Linux version</summary>

```bash
KEY=$(curl -s -XPOST localhost:18000/auth/keys -H 'Content-Type: application/json' -d '{"company_name":"Me","tier":"pro"}' | python -c "import sys,json;print(json.load(sys.stdin)['data']['api_key'])")
curl -s -XPOST localhost:18000/ask -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"question":"When did China ban gallium exports to the United States?"}' | python -m json.tool
```
</details>

## 3. Evaluation: see the scores table

The recorded numbers are in **`RESULTS.md`** and `evals/results/REPORT.md`. To reproduce them you need
a host Python environment.

```powershell
python -m venv .venv
.venv\Scripts\pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu
.venv\Scripts\pip install -r requirements-dev.txt -r evals/requirements.txt
$env:DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:15432/supplywatch"
```

**Fast, free check (retrieval only, no LLM, about 2 minutes):**
```powershell
.venv\Scripts\python -m evals.run_eval --mode vector --tag baseline --retrieval-only
.venv\Scripts\python -m evals.run_eval --mode rerank --tag improved --retrieval-only
```
You should see per-question lines, then a JSON summary with `"hit_at_5"`: about **0.95** for the
vector baseline and about **1.0** for rerank.

**Full run (answers + RAGAS), needs `ANTHROPIC_API_KEY`, about 25 minutes per config:**
```powershell
$env:LLM_BACKEND = "anthropic"; $env:ANTHROPIC_API_KEY = "sk-ant-..."
.venv\Scripts\python -m evals.run_eval --mode vector --tag baseline
.venv\Scripts\python -m evals.run_eval --mode rerank --tag improved
.venv\Scripts\python -m evals.report baseline improved
```
You should see a markdown table with faithfulness, answer relevancy, context precision and recall,
hit@5, refusal accuracy, latency and tokens for both configs. With `make` installed: `make eval`.
Your numbers will differ a little from `RESULTS.md`: the recorded run used the same model family
through the Claude Code CLI, and LLM judging is not deterministic.

## 4. Connect the MCP server to Claude Desktop

```powershell
.venv\Scripts\pip install -r mcp_server/requirements.txt    # if you skipped step 3
```

Open `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop → Settings → Developer → Edit Config)
and add the following. Use your own paths, and the `$key` from step 2 (`echo $key`):

```json
{
  "mcpServers": {
    "supplywatch": {
      "command": "C:\\Users\\YOU\\supplywatch-copilot\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\YOU\\supplywatch-copilot\\mcp_server\\server.py"],
      "env": {
        "SUPPLYWATCH_API_URL": "http://localhost:18000",
        "SUPPLYWATCH_API_KEY": "sw_PASTE_YOUR_KEY"
      }
    }
  }
}
```

Fully quit Claude Desktop (tray icon → Quit) and reopen it. In a new chat, the tools menu should list
**supplywatch** with 6 tools (`list_materials`, `get_material_signal`, `get_material_history`,
`supplier_exposure`, `search_documents`, `write_risk_report`). Ask:

> Using SupplyWatch, how risky is lithium right now, how has it moved this month, and which suppliers are exposed?

Claude should call `get_material_signal`, `get_material_history` and `supplier_exposure`, and answer
with the score (synthetic demo history) and the fictional suppliers (Halvorsen, Cordillera, Kairos).

You can use OAuth instead of an API key: replace `SUPPLYWATCH_API_KEY` with `SUPPLYWATCH_CLIENT_ID`
and `SUPPLYWATCH_CLIENT_SECRET` from step 6.

## 5. Simulate a risk spike and watch the alert arrive in n8n

```powershell
.venv\Scripts\python scripts/simulate_spike.py        # needs only httpx: pip install httpx
```
You should see something like this (recorded run: `examples/simulate_spike_output.txt`):
```
1. subscribed #6: Gallium (current score 84) threshold 89 -> http://n8n:5678/webhook/supplywatch-risk
   admin token via OAuth2 client credentials (client swc_..., expires in 900 s)
2. baseline score 79 (previous 84): alerts fired = 0
3. spike score 97: alerts fired = 1
   webhook delivery #... status=delivered attempts=1 event_id=...
4. n8n wrote to n8n_data/alerts.csv:
   2026-...,<event_id>,risk.threshold_crossed,Gallium,79,97,89,elevated,6
5. Telegram not configured -> telegram_mock.log: ... WOULD SEND: SupplyWatch alert: Gallium risk 79 -> 97 (threshold 89)
```
(If you run it twice, "alerts fired" counts the earlier run's subscription too. That's expected.)

```powershell
type n8n_data\alerts.csv
```
You should see the header and one row per run.

Open **http://localhost:15678**. The first time, n8n asks you to create a local owner account (any
email and password; it stays on your machine). Then open **Workflows → SupplyWatch risk alert →
Executions**. Each run shows *SupplyWatch webhook → Verify HMAC signature → Signature valid? → Append
to alerts.csv → Respond 200 → … → Log instead (Telegram not set)*.

Optional real Telegram: put `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`, run
`docker compose up -d n8n`, and run the simulation again.

Bad signatures are rejected. This returns **401** `{"ok":false,"reason":"bad signature"}`:
```powershell
curl.exe -s -X POST http://localhost:15678/webhook/supplywatch-risk -H "Content-Type: application/json" -H "X-SupplyWatch-Timestamp: 1" -H "X-SupplyWatch-Signature: sha256=00" -d "{}"
```
(With this old timestamp the reason is `missing or stale timestamp`. Also 401.)

## 6. OAuth2: get a token and call a protected route

```powershell
$c = (Invoke-RestMethod -Method Post -Uri http://localhost:18000/oauth/clients -Headers @{"X-API-Key"=$key} -ContentType 'application/json' -Body '{"name":"my-app","scopes":["read:materials"]}').data
$c        # client_id + client_secret (the secret is shown only once)
$t = Invoke-RestMethod -Method Post -Uri http://localhost:18000/oauth/token -ContentType 'application/x-www-form-urlencoded' -Body "grant_type=client_credentials&client_id=$($c.client_id)&client_secret=$($c.client_secret)"
$t        # access_token, token_type Bearer, expires_in 900, scope read:materials
Invoke-RestMethod -Uri http://localhost:18000/materials -Headers @{Authorization="Bearer $($t.access_token)"} | Select-Object -ExpandProperty data
```
You should see the 10 materials with their latest scores.

Scope denial: this token has no `ask` scope, so this fails with **403** `missing scope: ask`:
```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:18000/ask -Headers @{Authorization="Bearer $($t.access_token)"} -ContentType 'application/json' -Body '{"question":"lithium reserves?"}'
```
Tokens expire after 15 minutes (`JWT_TTL_SECONDS`). After that the same call returns **401** `token expired`.

## 7. Run the tests

Inside the API container (no host Python needed):
```powershell
docker compose exec -e LLM_BACKEND=fake api python -m pytest -q tests
```
You should see `... passed`. This covers the source SupplyWatch tests plus RAG, agent, webhooks,
OAuth and LLM-layer tests. The LLM is mocked.

Full suite, including the MCP stdio client test, from the host venv:
```powershell
$env:LLM_BACKEND = "fake"; .venv\Scripts\python -m pytest -q
```
You should see **93 passed**.

## 8. Stop

```powershell
docker compose down          # keeps data volumes
docker compose down -v       # also deletes the database and n8n data
```
