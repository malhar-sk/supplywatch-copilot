"""Force a risk-threshold crossing and watch the alert arrive in n8n.

    python scripts/simulate_spike.py [--material Gallium]

Steps: create an API key -> subscribe a webhook (n8n) with a threshold just above the
material's current score -> record a lower baseline score -> record a spike above the
threshold (admin endpoint) -> the API sends an HMAC-signed webhook (with retries) -> the
n8n workflow verifies the signature and appends a row to n8n_data/alerts.csv.

Needs only `httpx` (pip install httpx). Reads ADMIN_BOOTSTRAP_SECRET from .env.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def env_file() -> dict:
    p = ROOT / ".env"
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip('"')
    return out


def admin_headers(c: httpx.Client, api_key: str, cfg: dict) -> dict:
    """OAuth2 client-credentials token with the `admin` scope. Creating an admin-scoped
    client needs the ADMIN_BOOTSTRAP_SECRET from .env."""
    secret = os.environ.get("ADMIN_BOOTSTRAP_SECRET") or cfg.get("ADMIN_BOOTSTRAP_SECRET", "")
    r = c.post("/oauth/clients", headers={"X-API-Key": api_key, "X-Admin-Secret": secret},
               json={"name": "spike-simulator", "scopes": ["admin"]})
    if r.status_code != 200:
        raise SystemExit(f"could not create admin OAuth client ({r.status_code}): {r.text}. Is ADMIN_BOOTSTRAP_SECRET set in .env?")
    cred = r.json()["data"]
    tok = c.post("/oauth/token", data={"grant_type": "client_credentials", "client_id": cred["client_id"],
                                       "client_secret": cred["client_secret"], "scope": "admin"}).json()
    print(f"   admin token via OAuth2 client credentials (client {cred['client_id']}, expires in {tok['expires_in']} s)")
    return {"Authorization": f"Bearer {tok['access_token']}"}


def main():
    cfg = env_file()
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", f"http://localhost:{cfg.get('API_HOST_PORT', '18000')}"))
    ap.add_argument("--material", default="Gallium")
    ap.add_argument("--webhook-url", default=os.environ.get("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/supplywatch-risk"),
                    help="URL the API container calls (docker network name by default)")
    ap.add_argument("--csv", default=str(ROOT / "n8n_data" / "alerts.csv"))
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api, timeout=60)

    key = c.post("/auth/keys", json={"company_name": "Spike simulation", "tier": "pro"}).json()["data"]["api_key"]
    h = {"X-API-Key": key}
    sig = c.get("/tools/signal", params={"material": a.material}, headers=h).json()["data"]
    mid, current = sig["material_id"], sig["score"]
    threshold = min(95, current + 5)
    sub = c.post("/alerts/subscribe", headers=h, json={"material_id": mid, "threshold": threshold,
                                                       "webhook_url": a.webhook_url}).json()["data"]
    print(f"1. subscribed #{sub['id']}: {a.material} (current score {current}) threshold {threshold} -> {a.webhook_url}")

    adm = admin_headers(c, key, cfg)
    base = c.post(f"/admin/materials/{mid}/score", headers=adm, json={"score": threshold - 10}).json()["data"]
    print(f"2. baseline score {base['score']} (previous {base['previous_score']}): alerts fired = {len(base['alerts_fired'])}")
    spike = c.post(f"/admin/materials/{mid}/score", headers=adm, json={"score": threshold + 8,
                   "factors": {"price": 90, "export": 95, "trade": 85, "simulated": 1}}).json()["data"]
    fired = [f for f in spike["alerts_fired"] if f["subscription_id"] == sub["id"]]
    print(f"3. spike score {spike['score']}: alerts fired = {len(spike['alerts_fired'])}")
    if not fired:
        raise SystemExit("no alert fired for our subscription")
    f = fired[0]
    print(f"   webhook delivery #{f.get('delivery_id')} status={f.get('delivery_status')} attempts={f.get('attempts')} "
          f"event_id={f.get('event_id')}")
    log = c.get("/webhooks/deliveries", headers=h, params={"limit": 1}).json()["data"]
    if log:
        print(f"   delivery log: {log[0]['attempt_log']}")

    csv = Path(a.csv)
    for _ in range(20):
        if csv.exists() and f.get("event_id", "?") in csv.read_text(encoding="utf-8"):
            row = [r for r in csv.read_text(encoding="utf-8").splitlines() if f["event_id"] in r][0]
            print(f"4. n8n wrote to {csv.relative_to(ROOT) if csv.is_relative_to(ROOT) else csv}:\n   {row}")
            break
        time.sleep(0.5)
    else:
        print(f"4. row not found in {csv} (if n8n runs elsewhere, check its execution list)")
    mock = csv.parent / "telegram_mock.log"
    for _ in range(10):  # the Telegram/log branch runs just after the CSV append
        if mock.exists() and f.get("event_id", "?") and "WOULD SEND" in mock.read_text(encoding="utf-8"):
            break
        time.sleep(0.5)
    if mock.exists():
        print(f"5. Telegram not configured -> {mock.name}: {mock.read_text(encoding='utf-8').splitlines()[-1]}")


if __name__ == "__main__":
    main()
