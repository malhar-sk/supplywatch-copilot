"""Signed webhook delivery with retry + exponential backoff, logged to webhook_deliveries."""
from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from webhooks.models import WebhookDelivery
from webhooks.signing import sign

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


def canonical_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()


async def deliver(db: AsyncSession, url: str, payload: dict, event_type: str = "risk.threshold_crossed",
                  subscription_id: int | None = None, client: httpx.AsyncClient | None = None,
                  sleep=asyncio.sleep) -> WebhookDelivery:
    s = get_settings()
    event_id = payload.get("event_id") or str(uuid.uuid4())
    payload = {**payload, "event_id": event_id, "event_type": event_type}
    body = canonical_body(payload)
    row = WebhookDelivery(event_id=event_id, event_type=event_type, subscription_id=subscription_id, url=url,
                          payload=payload, status="pending", attempts=0, attempt_log=[])
    db.add(row)
    await db.flush()
    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    log = []
    try:
        for attempt in range(1, s.webhook_max_attempts + 1):
            ts = str(int(time.time()))
            headers = {"Content-Type": "application/json", "User-Agent": "SupplyWatch-Webhooks/1.0",
                       "X-SupplyWatch-Event": event_type, "X-SupplyWatch-Delivery": event_id,
                       "X-SupplyWatch-Timestamp": ts,
                       "X-SupplyWatch-Signature": sign(s.webhook_signing_secret, ts, body)}
            entry = {"attempt": attempt, "at": datetime.now(timezone.utc).isoformat()}
            retry = False
            try:
                r = await client.post(url, content=body, headers=headers)
                entry["status_code"] = r.status_code
                row.last_status_code = r.status_code
                if 200 <= r.status_code < 300:
                    row.status, row.delivered_at, row.last_error = "delivered", datetime.now(timezone.utc), None
                else:
                    row.last_error = f"HTTP {r.status_code}: {r.text[:200]}"
                    retry = r.status_code in RETRY_STATUS
            except httpx.HTTPError as e:
                entry["error"] = row.last_error = f"{type(e).__name__}: {e}"[:300]
                retry = True
            row.attempts = attempt
            log.append(entry)
            if row.status == "delivered" or not retry or attempt == s.webhook_max_attempts:
                break
            delay = s.webhook_backoff_base_seconds * (2 ** (attempt - 1)) * (1 + random.random() * 0.25)
            entry["retry_in_s"] = round(delay, 2)
            await sleep(delay)
        if row.status != "delivered":
            row.status = "failed"
    finally:
        row.attempt_log = log
        if own:
            await client.aclose()
    await db.flush()
    return row
