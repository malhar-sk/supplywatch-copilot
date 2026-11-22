"""M6: HMAC signing, retry/backoff, delivery log, edge-triggered threshold crossings."""
import asyncio
import json
import time

import httpx
import pytest
from sqlalchemy import select

from core.config import get_settings
from webhooks.alerts import crossed
from webhooks.signing import sign, verify
from tests.helpers import seed_db


def test_sign_verify_roundtrip_and_tamper():
    ts = str(int(time.time()))
    body = b'{"score":80}'
    sig = sign("s3cret", ts, body)
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    assert verify("s3cret", ts, body, sig)
    assert not verify("s3cret", ts, b'{"score":81}', sig)          # body tampered
    assert not verify("other", ts, body, sig)                       # wrong secret
    assert not verify("s3cret", str(int(ts) - 301), body, sign("s3cret", str(int(ts) - 301), body))  # replay
    assert not verify("s3cret", "not-a-number", body, sig)


@pytest.mark.parametrize("prev,new,thr,expected", [
    (60, 70, 65, True), (64, 65, 65, True), (None, 80, 65, True),
    (66, 80, 65, False),   # already above: no re-alert
    (70, 60, 65, False),   # falling
    (50, 60, 65, False),
])
def test_crossed(prev, new, thr, expected):
    assert crossed(prev, new, thr) is expected


class _FakeDB:
    def __init__(self):
        self.added = []

    def add(self, o):
        self.added.append(o)

    async def flush(self):
        pass


def _deliver(handler, sleeps):
    from webhooks.delivery import deliver

    async def fake_sleep(s):
        sleeps.append(s)

    async def _run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await deliver(_FakeDB(), "http://receiver.test/hook", {"score": 80}, client=client, sleep=fake_sleep)

    return asyncio.run(_run())


def test_retry_with_backoff_then_success_and_receiver_can_verify():
    calls, sleeps = [], []
    secret = get_settings().webhook_signing_secret

    def handler(req: httpx.Request):
        calls.append(req)
        ok = verify(secret, req.headers["X-SupplyWatch-Timestamp"], req.content, req.headers["X-SupplyWatch-Signature"])
        assert ok, "receiver must be able to verify every attempt"
        return httpx.Response(503 if len(calls) < 3 else 200)

    row = _deliver(handler, sleeps)
    assert row.status == "delivered" and row.attempts == 3
    assert [e["status_code"] for e in row.attempt_log] == [503, 503, 200]
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]          # exponential backoff
    assert len({c.headers["X-SupplyWatch-Delivery"] for c in calls}) == 1  # same event id on retries
    body = json.loads(calls[0].content)
    assert body["event_type"] == "risk.threshold_crossed" and body["score"] == 80


def test_client_error_is_not_retried():
    sleeps = []
    row = _deliver(lambda req: httpx.Response(400, text="bad"), sleeps)
    assert row.status == "failed" and row.attempts == 1 and sleeps == []


def test_network_errors_exhaust_attempts():
    def handler(req):
        raise httpx.ConnectError("refused")

    sleeps = []
    row = _deliver(handler, sleeps)
    assert row.status == "failed" and row.attempts == get_settings().webhook_max_attempts
    assert "ConnectError" in row.last_error


def test_record_score_fires_once_on_crossing_and_logs_delivery():
    seed_db()
    from core.database import AlertHistory, AlertSubscription, ApiKey, AsyncSessionLocal, Material
    from webhooks.alerts import record_score
    from webhooks.models import WebhookDelivery

    received = []

    def handler(req):
        received.append(json.loads(req.content))
        return httpx.Response(200)

    async def _run():
        async with AsyncSessionLocal() as db, httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            m = (await db.execute(select(Material).where(Material.name == "Manganese"))).scalar_one()
            key = ApiKey(key_hash=f"test-{time.time_ns()}", company_name="hooks", tier="pro")
            db.add(key)
            await db.flush()
            sub = AlertSubscription(api_key_id=key.id, material_id=m.id, threshold=90,
                                    webhook_url="http://receiver.test/hook")
            db.add(sub)
            await db.flush()
            first = await record_score(db, m, 40, client=client)        # below
            second = await record_score(db, m, 95, client=client)       # crosses 90
            third = await record_score(db, m, 97, client=client)        # still above: no re-alert
            await db.commit()
            deliveries = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.subscription_id == sub.id))).scalars().all()
            hist = (await db.execute(select(AlertHistory).where(AlertHistory.subscription_id == sub.id))).scalars().all()
            return first, second, third, deliveries, hist

    first, second, third, deliveries, hist = asyncio.run(_run())
    sub_id = deliveries[0].subscription_id if deliveries else None
    mine = lambda r: [f for f in r["alerts_fired"] if f["subscription_id"] == sub_id]  # noqa: E731
    assert mine(first) == [] and mine(third) == []
    assert len(mine(second)) == 1 and mine(second)[0]["delivery_status"] == "delivered"
    assert len(deliveries) == 1 and deliveries[0].status == "delivered" and len(hist) == 1
    got = [r for r in received if r["subscription_id"] == sub_id]
    assert len(got) == 1 and got[0]["previous_score"] == 40 and got[0]["score"] == 95 and got[0]["threshold"] == 90
