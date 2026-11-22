"""HMAC-SHA256 webhook signatures.

Headers sent with every delivery:
  X-SupplyWatch-Event:      risk.threshold_crossed
  X-SupplyWatch-Delivery:   <event uuid>          (idempotency key for receivers)
  X-SupplyWatch-Timestamp:  <unix seconds>
  X-SupplyWatch-Signature:  sha256=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>

Receivers recompute the HMAC over the *raw* body bytes, compare in constant
time, and reject timestamps older than 5 minutes (replay protection).
"""
from __future__ import annotations

import hashlib
import hmac
import time

TOLERANCE_SECONDS = 300


def sign(secret: str, timestamp: str, body: bytes) -> str:
    mac = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def verify(secret: str, timestamp: str, body: bytes, signature: str, now: float | None = None) -> bool:
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs((now or time.time()) - ts) > TOLERANCE_SECONDS:
        return False
    return hmac.compare_digest(sign(secret, timestamp, body), signature or "")
