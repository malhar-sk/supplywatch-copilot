"""POST /oauth/clients (create a client, needs an API key) and POST /oauth/token
(RFC 6749 section 4.4 client-credentials grant; form or JSON body, or HTTP Basic)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import require_api_key
from core.config import get_settings
from core.database import ApiKey, get_db
from oauth.models import OAuthClient
from oauth.tokens import ALL_SCOPES, issue

router = APIRouter(prefix="/oauth", tags=["oauth"])


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class ClientIn(BaseModel):
    name: str
    scopes: list[str] = ["read:materials", "ask"]


@router.post("/clients")
async def create_client(payload: ClientIn, api_key: ApiKey = Depends(require_api_key),
                        x_admin_secret: str | None = Header(default=None), db: AsyncSession = Depends(get_db)):
    bad = [s for s in payload.scopes if s not in ALL_SCOPES]
    if bad or not payload.scopes:
        raise HTTPException(status_code=422, detail=f"unknown scopes {bad}; allowed: {list(ALL_SCOPES)}")
    if "admin" in payload.scopes:
        boot = get_settings().admin_bootstrap_secret
        if not boot or not x_admin_secret or not hmac.compare_digest(boot, x_admin_secret):
            raise HTTPException(status_code=403, detail="the admin scope needs a valid X-Admin-Secret header")
    client_id = f"swc_{secrets.token_hex(8)}"
    secret = f"sws_{secrets.token_urlsafe(32)}"
    db.add(OAuthClient(client_id=client_id, secret_hash=_hash(secret), name=payload.name, scopes=payload.scopes,
                       api_key_id=api_key.id))
    await db.commit()
    return {"data": {"client_id": client_id, "client_secret": secret, "scopes": payload.scopes,
                     "note": "Store the secret now; it is not shown again."}}


def _oauth_error(status: int, error: str, desc: str) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="supplywatch"'
    return JSONResponse(status_code=status, content={"error": error, "error_description": desc}, headers=headers)


async def _read_params(request: Request) -> dict:
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            return {k: str(v) for k, v in json.loads(raw or b"{}").items()}
        except (ValueError, AttributeError):
            return {}
    return {k: v[0] for k, v in parse_qs(raw.decode()).items()}


@router.post("/token")
async def token(request: Request, db: AsyncSession = Depends(get_db)):
    p = await _read_params(request)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            cid, _, sec = base64.b64decode(auth[6:]).decode().partition(":")
            p.setdefault("client_id", cid)
            p.setdefault("client_secret", sec)
        except ValueError:
            return _oauth_error(401, "invalid_client", "malformed Basic credentials")
    if p.get("grant_type") != "client_credentials":
        return _oauth_error(400, "unsupported_grant_type", "only grant_type=client_credentials is supported")
    cid, sec = p.get("client_id", ""), p.get("client_secret", "")
    client = (await db.execute(select(OAuthClient).where(OAuthClient.client_id == cid))).scalar_one_or_none()
    if not client or client.revoked or not hmac.compare_digest(client.secret_hash, _hash(sec)):
        return _oauth_error(401, "invalid_client", "unknown client or bad secret")
    requested = p.get("scope", "").split() or list(client.scopes)
    if not set(requested) <= set(client.scopes):
        return _oauth_error(400, "invalid_scope", f"client may request only: {' '.join(client.scopes)}")
    access, ttl = issue(client.client_id, client.api_key_id, requested)
    return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": ttl, "scope": " ".join(requested)},
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
