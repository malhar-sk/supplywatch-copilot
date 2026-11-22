# SupplyWatch Copilot: onboarding guide for your engineering team

This guide is for the engineer integrating SupplyWatch at your company. In about 30 minutes you will:

1. get credentials (an API key, or OAuth2 client credentials),
2. make your first API calls (risk signal and a cited question),
3. route risk alerts into your own tools with signed webhooks and n8n,
4. connect Claude (or any MCP client) so analysts can ask questions in chat,
5. know what to do when something goes wrong.

Base URL in this guide: `http://localhost:18000` (local install). Replace it with your SupplyWatch URL.
Interactive reference: `GET /docs` (OpenAPI).

> **Demo data notice.** The local install ships with *synthetic* 30-day score history and a
> *fictional* supplier directory (for example "Kivu Cobalt Logistics" and "Acme Drives"). Document
> answers cite real USGS Mineral Commodity Summaries 2024/2025 text.

---

## 1. Credentials

### Option A: API key (simplest)

```bash
curl -s -X POST http://localhost:18000/auth/keys \
  -H 'Content-Type: application/json' -d '{"company_name":"Your Co","tier":"pro"}'
# -> {"data":{"api_key":"sw_...","tier":"pro"}, ...}
```
Send it as `X-API-Key: sw_...`. It grants the `read:materials` and `ask` scopes. Free-tier keys are
limited to 100 requests a day. Store keys in a secrets manager: SupplyWatch only keeps a SHA-256 hash.

### Option B: OAuth2 client credentials (recommended for services)

Create a client with the scopes the integration needs:

```bash
curl -s -X POST http://localhost:18000/oauth/clients -H "X-API-Key: $KEY" \
  -H 'Content-Type: application/json' -d '{"name":"erp-sync","scopes":["read:materials"]}'
# -> {"data":{"client_id":"swc_...","client_secret":"sws_...", ...}}   (secret shown once)
```

Exchange the credentials for a short-lived token (RFC 6749 §4.4; form body or HTTP Basic auth):

```bash
curl -s -X POST http://localhost:18000/oauth/token \
  -d grant_type=client_credentials -d client_id=$CID -d client_secret=$SECRET
# -> {"access_token":"eyJ...","token_type":"Bearer","expires_in":900,"scope":"read:materials"}
```

Call the API with `Authorization: Bearer <access_token>`. Cache the token and fetch a new one when
`expires_in` runs out. Don't request a token for every call.

| Scope | Allows |
|---|---|
| `read:materials` | `GET /materials`, `/materials/{id}/signal`, `/materials/{id}/history`, `/tools/signal`, `/tools/history`, `/tools/supplier-exposure` |
| `ask` | `POST /ask`, `POST /agent`, `/tools/search`, `/tools/risk-report` |
| `admin` | `POST /admin/materials/{id}/score` (operators only; creating an admin client needs the deployment's `X-Admin-Secret`) |

You can request a subset of a client's scopes (`-d scope=read:materials`), but never more than it has.

## 2. First calls

```bash
# all tracked materials with their latest 0-100 disruption score
curl -s http://localhost:18000/materials -H "X-API-Key: $KEY"

# one material's signal: score, band, factor breakdown
curl -s "http://localhost:18000/tools/signal?material=Gallium" -H "X-API-Key: $KEY"

# a grounded question: answer + citations (title, source URL, snippet)
curl -s -X POST http://localhost:18000/ask -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"question":"Which countries supply most of the cobalt the US imports?"}'

# the analyst agent: picks tools itself and returns its tool-call trace
curl -s -X POST http://localhost:18000/agent -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"question":"Write a risk report for antimony"}'
```

How to read `/ask` responses:
- `refused: true` means the copilot found nothing relevant enough and says so. It won't guess.
- `citations[].synthetic: true` means the passage comes from demo data, not a public source.
- `top_similarity` is how close the best passage was (0–1). Answers below `REFUSAL_MIN_SIMILARITY`
  (default 0.62) are refused without calling the LLM.

## 3. Alerts into your tools (webhooks + n8n)

Subscribe a webhook to a material and threshold:

```bash
curl -s -X POST http://localhost:18000/alerts/subscribe -H "X-API-Key: $KEY" -H 'Content-Type: application/json' \
  -d '{"material_id": 5, "threshold": 75, "webhook_url": "https://your-n8n.example.com/webhook/supplywatch-risk"}'
```

When the score **crosses** your threshold upward (for example 72 → 78 with threshold 75),
SupplyWatch POSTs one event. It does not repeat the event every scoring run while the score stays high.

```
POST <your url>
Content-Type: application/json
X-SupplyWatch-Event: risk.threshold_crossed
X-SupplyWatch-Delivery: 3084592e-...        (unique per event; use it to de-duplicate)
X-SupplyWatch-Timestamp: 1795000000
X-SupplyWatch-Signature: sha256=<hex>

{"band":"elevated","event_id":"3084592e-...","event_type":"risk.threshold_crossed","factors":{...},
 "material":{"id":5,"name":"Gallium","symbol":"Ga"},"message":"...","occurred_at":"...",
 "previous_score":72,"score":78,"subscription_id":12,"threshold":75}
```

**Verify every request.** Compute `HMAC-SHA256(secret, "<timestamp>.<raw body bytes>")`, hex-encode it,
prefix it with `sha256=`, compare in constant time, and reject timestamps more than 5 minutes old.

```python
import hmac, hashlib, time
def valid(secret: str, ts: str, raw: bytes, sig: str) -> bool:
    if abs(time.time() - int(ts)) > 300: return False
    mac = hmac.new(secret.encode(), ts.encode() + b"." + raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest("sha256=" + mac, sig)
```

**Delivery behaviour.** Your endpoint should answer 2xx within 10 s. Network errors and
408/425/429/5xx responses are retried up to 4 attempts with exponential backoff (0.5 s, 1 s, 2 s,
plus jitter). Other 4xx responses are not retried. Every attempt is logged. Inspect them with
`GET /webhooks/deliveries`. Send yourself a signed test event with
`POST /webhooks/test {"url": "..."}`.

**Using n8n.** Import `n8n/workflows/risk_alert.json` (n8n → Workflows → Import from file) and set
these environment variables on your n8n instance: `WEBHOOK_SIGNING_SECRET` (the same value as
SupplyWatch), `NODE_FUNCTION_ALLOW_BUILTIN=crypto,fs`, `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`, and
optionally `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`. The workflow:

```
Webhook (raw body) → Verify HMAC signature → valid? ─ no → 401
                                              └ yes → Append to alerts.csv → 200 → Telegram (or log)
```
To send alerts to Slack, Teams, Jira or a Google Sheet instead, replace the *Append to alerts.csv*
node with the matching n8n node. The verified payload is in `$json.payload`.

## 4. Connect Claude via MCP

`mcp_server/server.py` is a stdio MCP server. It runs on the analyst's machine and calls your
SupplyWatch API. Install it with `pip install -r mcp_server/requirements.txt`, then add this to
Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "supplywatch": {
      "command": "python",
      "args": ["/path/to/supplywatch-copilot/mcp_server/server.py"],
      "env": {
        "SUPPLYWATCH_API_URL": "http://localhost:18000",
        "SUPPLYWATCH_CLIENT_ID": "swc_...",
        "SUPPLYWATCH_CLIENT_SECRET": "sws_..."
      }
    }
  }
}
```
(`SUPPLYWATCH_API_KEY` works instead of the client pair.) Tools: `list_materials`,
`get_material_signal`, `get_material_history`, `supplier_exposure`, `search_documents`,
`write_risk_report`. Use a client with only `read:materials ask` for analysts.

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `401 X-API-Key is required` | No credentials sent | Add `X-API-Key` or `Authorization: Bearer ...` |
| `401 Invalid API key` | Typo, or the key was created on another deployment | Create a new key with `POST /auth/keys` |
| `401 token expired` | Access tokens last 15 min | Request a new token. Cache it until `expires_in` runs out |
| `403 missing scope: ask` | The token or client lacks the scope | Create a client with that scope, or request it in `scope=` |
| `400 invalid_scope` at `/oauth/token` | You asked for more scopes than the client has | Request a subset |
| `401 invalid_client` | Wrong client secret, or the client was revoked | Create a new client (secrets can't be recovered) |
| `429 Daily request limit reached` | Free tier: 100 requests a day | Use a `pro` key |
| `/ask` always says "I don't know" | Corpus not ingested, or the question is off-topic | Run `python scripts/seed.py`. Check `top_similarity` |
| Answer ends with "(extractive answer: no LLM configured)" | No `ANTHROPIC_API_KEY` on the API | Set it in `.env`, then `docker compose up -d api` |
| n8n returns `401 bad signature` | Different `WEBHOOK_SIGNING_SECRET` on the two sides, or the body was changed by a proxy | Use the same secret. Sign and verify the raw body |
| n8n returns `401 missing or stale timestamp` | Clock skew above 5 min, or a replayed event | Sync clocks (NTP). Don't replay old events |
| n8n 404 "webhook not registered" | Workflow not active | Activate it in the n8n UI (top-right toggle) |
| `GET /webhooks/deliveries` shows `failed`, `ConnectError` | SupplyWatch can't reach your URL | Inside Docker use the service name (`http://n8n:5678/...`), not `localhost` |
| Claude Desktop shows no SupplyWatch tools | Bad path or Python in the config, or Claude not restarted | Use absolute paths. Fully quit and reopen Claude. Check Claude's MCP logs |
| MCP tool returns `SupplyWatch API unreachable` | API not running, or wrong `SUPPLYWATCH_API_URL` | `curl <url>/health` |
