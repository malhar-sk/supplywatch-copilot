"""Webhook delivery log, signed test pings, and the admin score endpoint used by
scripts/simulate_spike.py to force a threshold crossing."""
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from core.config import get_settings
from core.database import AlertSubscription, ApiKey, Material, get_db
from webhooks.alerts import record_score
from webhooks.delivery import deliver
from webhooks.models import WebhookDelivery

router = APIRouter(tags=["webhooks"])


def _env(data):
    return {"data": data, "meta": {"timestamp": datetime.now(timezone.utc), "version": get_settings().app_version}}


async def require_admin(x_admin_secret: str | None = Header(default=None)):
    secret = get_settings().admin_bootstrap_secret
    if not secret or not x_admin_secret or not hmac.compare_digest(secret, x_admin_secret):
        raise HTTPException(status_code=403, detail="admin access required")


class ScoreIn(BaseModel):
    score: int = Field(ge=0, le=100)
    factors: dict | None = None


class PingIn(BaseModel):
    url: str


def _row(d: WebhookDelivery) -> dict:
    return {"id": d.id, "event_id": d.event_id, "event_type": d.event_type, "subscription_id": d.subscription_id,
            "url": d.url, "status": d.status, "attempts": d.attempts, "last_status_code": d.last_status_code,
            "last_error": d.last_error, "attempt_log": d.attempt_log, "created_at": d.created_at,
            "delivered_at": d.delivered_at}


@router.post("/admin/materials/{material_id}/score", dependencies=[Depends(require_admin)])
async def admin_record_score(material_id: int, payload: ScoreIn, db: AsyncSession = Depends(get_db)):
    m = await db.get(Material, material_id)
    if not m:
        raise HTTPException(status_code=404, detail="material not found")
    out = await record_score(db, m, payload.score, payload.factors or {"manual": payload.score})
    await db.commit()
    return _env(out)


@router.get("/webhooks/deliveries")
async def list_deliveries(limit: int = Query(20, ge=1, le=200), api_key: ApiKey = Depends(require_api_key),
                          db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(WebhookDelivery).join(AlertSubscription, AlertSubscription.id == WebhookDelivery.subscription_id)
        .where(AlertSubscription.api_key_id == api_key.id).order_by(desc(WebhookDelivery.id)).limit(limit))).scalars().all()
    return _env([_row(d) for d in rows])


@router.post("/webhooks/test")
async def send_test(payload: PingIn, api_key: ApiKey = Depends(require_api_key), db: AsyncSession = Depends(get_db)):
    d = await deliver(db, payload.url, {"message": "SupplyWatch test event", "company": api_key.company_name},
                      event_type="ping")
    await db.commit()
    return _env(_row(d))
