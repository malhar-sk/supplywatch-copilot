"""Threshold-crossing alerts (edge-triggered).

A subscription fires when a new score crosses its threshold upward:
previous < threshold <= new (or there was no previous score and new >= threshold).
The source pipeline fired on every run while score >= threshold (level-triggered),
which re-alerts every 6 hours; crossing-based firing is what a webhook consumer wants.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from alerts.dispatcher import AlertDispatcher
from core.database import AlertHistory, AlertSubscription, DisruptionScore, Material
from webhooks.delivery import deliver


def crossed(previous: int | None, new: int, threshold: int) -> bool:
    return new >= threshold and (previous is None or previous < threshold)


async def latest_score(db: AsyncSession, material_id: int) -> int | None:
    return (await db.execute(select(DisruptionScore.score).where(DisruptionScore.material_id == material_id)
                             .order_by(desc(DisruptionScore.computed_at), desc(DisruptionScore.id)).limit(1))
            ).scalar_one_or_none()


async def process_new_score(db: AsyncSession, material: Material, new_score: int, previous: int | None,
                            factors: dict | None = None, client=None) -> list[dict]:
    """Fire alerts for every subscription this score change crosses. Caller commits."""
    subs = (await db.execute(select(AlertSubscription).where(AlertSubscription.material_id == material.id))).scalars().all()
    fired = []
    dispatcher = AlertDispatcher()
    for sub in subs:
        if not crossed(previous, new_score, sub.threshold):
            continue
        msg = (f"SupplyWatch alert: {material.name} risk score rose from {previous if previous is not None else 'n/a'} "
               f"to {new_score}, crossing your threshold of {sub.threshold}")
        result = {"subscription_id": sub.id, "message": msg}
        if sub.email:
            await dispatcher.send_email(sub.email, f"Supply risk alert: {material.name}", msg)
        if sub.webhook_url:
            payload = {"material": {"id": material.id, "name": material.name, "symbol": material.symbol},
                       "previous_score": previous, "score": new_score, "threshold": sub.threshold,
                       "band": "elevated" if new_score >= 70 else "moderate" if new_score >= 40 else "low",
                       "factors": factors or {}, "message": msg, "subscription_id": sub.id,
                       "occurred_at": datetime.now(timezone.utc).isoformat()}
            d = await deliver(db, sub.webhook_url, payload, subscription_id=sub.id, client=client)
            result.update(delivery_id=d.id, delivery_status=d.status, attempts=d.attempts, event_id=d.event_id)
        db.add(AlertHistory(subscription_id=sub.id, score_at_trigger=new_score, message=msg))
        fired.append(result)
    return fired


async def record_score(db: AsyncSession, material: Material, score: int, factors: dict | None = None,
                       client=None) -> dict:
    previous = await latest_score(db, material.id)
    db.add(DisruptionScore(material_id=material.id, score=score, factors=factors or {}))
    fired = await process_new_score(db, material, score, previous, factors, client=client)
    return {"material": material.name, "previous_score": previous, "score": score, "alerts_fired": fired}
