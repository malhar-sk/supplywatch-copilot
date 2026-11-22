"""Generates n8n/workflows/risk_alert.json (kept as code so the JS stays readable).

    python n8n/build_workflow.py
"""
import json
from pathlib import Path

VERIFY_JS = r"""
// Verify the SupplyWatch HMAC-SHA256 signature over the RAW request body.
const crypto = require('crypto');
const secret = $env.WEBHOOK_SIGNING_SECRET || '';
const out = [];
for (let i = 0; i < $input.all().length; i++) {
  const item = $input.all()[i];
  const h = item.json.headers || {};
  let raw;
  if (item.binary && item.binary.data) {
    raw = await this.helpers.getBinaryDataBuffer(i, 'data');
  } else {
    raw = Buffer.from(JSON.stringify(item.json.body || {}));
  }
  const ts = h['x-supplywatch-timestamp'] || '';
  const sig = h['x-supplywatch-signature'] || '';
  const expected = 'sha256=' + crypto.createHmac('sha256', secret).update(ts + '.').update(raw).digest('hex');
  const age = Math.abs(Date.now() / 1000 - parseInt(ts, 10));
  let valid = false, reason = 'ok';
  if (!secret) reason = 'WEBHOOK_SIGNING_SECRET not set in n8n';
  else if (!ts || isNaN(age) || age > 300) reason = 'missing or stale timestamp';
  else if (sig.length !== expected.length) reason = 'bad signature';
  else if (!crypto.timingSafeEqual(Buffer.from(sig), Buffer.from(expected))) reason = 'bad signature';
  else valid = true;
  let payload = {};
  try { payload = JSON.parse(raw.toString('utf8')); } catch (e) { reason = valid ? 'invalid JSON' : reason; valid = false; }
  out.push({ json: { valid, reason, event_id: h['x-supplywatch-delivery'] || '', event_type: h['x-supplywatch-event'] || '', payload } });
}
return out;
"""

CSV_JS = r"""
// Append one row per verified alert to /home/node/alerts/alerts.csv (mounted from ./n8n_data).
const fs = require('fs');
const path = '/home/node/alerts/alerts.csv';
const header = 'received_at,event_id,event_type,material,previous_score,score,threshold,band,subscription_id\n';
if (!fs.existsSync(path)) fs.writeFileSync(path, header);
const esc = (v) => { const s = (v === null || v === undefined) ? '' : String(v); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
for (const item of $input.all()) {
  const p = item.json.payload || {};
  const row = [new Date().toISOString(), item.json.event_id, item.json.event_type, (p.material || {}).name,
               p.previous_score, p.score, p.threshold, p.band, p.subscription_id].map(esc).join(',') + '\n';
  fs.appendFileSync(path, row);
}
return $input.all();
"""

TELEGRAM_CHECK_JS = r"""
// Route: Telegram if both env vars are set, otherwise mock (log file).
const on = Boolean($env.TELEGRAM_BOT_TOKEN) && Boolean($env.TELEGRAM_CHAT_ID);
return $input.all().map(i => ({ json: { ...i.json, telegram_enabled: on,
  text: `SupplyWatch alert: ${(i.json.payload.material || {}).name} risk ${i.json.payload.previous_score} -> ${i.json.payload.score} (threshold ${i.json.payload.threshold})` } }));
"""

MOCK_LOG_JS = r"""
// TELEGRAM_BOT_TOKEN not set: write what would have been sent.
const fs = require('fs');
for (const item of $input.all()) {
  fs.appendFileSync('/home/node/alerts/telegram_mock.log', `${new Date().toISOString()} WOULD SEND: ${item.json.text}\n`);
}
return $input.all();
"""


def node(id_, name, type_, version, pos, params, **extra):
    return {"id": id_, "name": name, "type": type_, "typeVersion": version, "position": pos, "parameters": params, **extra}


def code(id_, name, js, pos):
    return node(id_, name, "n8n-nodes-base.code", 2, pos, {"mode": "runOnceForAllItems", "jsCode": js.strip()})


def if_node(id_, name, left, pos):
    return node(id_, name, "n8n-nodes-base.if", 2, pos, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict"},
                       "conditions": [{"id": f"{id_}-c", "leftValue": left, "rightValue": "",
                                       "operator": {"type": "boolean", "operation": "true", "singleValue": True}}],
                       "combinator": "and"},
        "options": {}})


workflow = {
    "id": "swRiskAlert01",
    "name": "SupplyWatch risk alert",
    "active": True,
    "settings": {"executionOrder": "v1"},
    "nodes": [
        node("n1", "SupplyWatch webhook", "n8n-nodes-base.webhook", 2, [0, 300],
             {"httpMethod": "POST", "path": "supplywatch-risk", "responseMode": "responseNode",
              "options": {"rawBody": True}}, webhookId="5b0c1e8e-2f0a-4c7e-9a51-supplywatch01"),
        code("n2", "Verify HMAC signature", VERIFY_JS, [220, 300]),
        if_node("n3", "Signature valid?", "={{ $json.valid }}", [440, 300]),
        code("n4", "Append to alerts.csv", CSV_JS, [660, 200]),
        node("n5", "Respond 200", "n8n-nodes-base.respondToWebhook", 1.1, [880, 200],
             {"respondWith": "json", "responseBody": "={{ { ok: true, event_id: $json.event_id } }}", "options": {}}),
        node("n6", "Respond 401", "n8n-nodes-base.respondToWebhook", 1.1, [660, 420],
             {"respondWith": "json", "responseBody": "={{ { ok: false, reason: $json.reason } }}",
              "options": {"responseCode": 401}}),
        code("n7", "Telegram configured?", TELEGRAM_CHECK_JS, [1100, 200]),
        if_node("n8", "Send Telegram?", "={{ $json.telegram_enabled }}", [1320, 200]),
        node("n9", "Telegram sendMessage", "n8n-nodes-base.httpRequest", 4.2, [1540, 100],
             {"method": "POST", "url": "=https://api.telegram.org/bot{{ $env.TELEGRAM_BOT_TOKEN }}/sendMessage",
              "sendBody": True, "specifyBody": "json",
              "jsonBody": "={{ JSON.stringify({ chat_id: $env.TELEGRAM_CHAT_ID, text: $json.text }) }}", "options": {}}),
        code("n10", "Log instead (Telegram not set)", MOCK_LOG_JS, [1540, 300]),
    ],
    "connections": {
        "SupplyWatch webhook": {"main": [[{"node": "Verify HMAC signature", "type": "main", "index": 0}]]},
        "Verify HMAC signature": {"main": [[{"node": "Signature valid?", "type": "main", "index": 0}]]},
        "Signature valid?": {"main": [[{"node": "Append to alerts.csv", "type": "main", "index": 0}],
                                      [{"node": "Respond 401", "type": "main", "index": 0}]]},
        "Append to alerts.csv": {"main": [[{"node": "Respond 200", "type": "main", "index": 0}]]},
        "Respond 200": {"main": [[{"node": "Telegram configured?", "type": "main", "index": 0}]]},
        "Telegram configured?": {"main": [[{"node": "Send Telegram?", "type": "main", "index": 0}]]},
        "Send Telegram?": {"main": [[{"node": "Telegram sendMessage", "type": "main", "index": 0}],
                                    [{"node": "Log instead (Telegram not set)", "type": "main", "index": 0}]]},
    },
    "pinData": {},
}

if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "workflows" / "risk_alert.json"
    out.write_text(json.dumps(workflow, indent=2), encoding="utf-8")
    print("wrote", out)
