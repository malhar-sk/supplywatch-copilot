import hashlib
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import get_settings
from core.database import ApiKey, ApiUsageCounter, get_db

# Copilot addition: every protected route accepts EITHER the original X-API-Key OR an
# OAuth2 bearer token (client-credentials JWT, see oauth/). Both resolve to the owning
# ApiKey row, so per-company data (alerts, deliveries) and the free-tier daily limit work
# the same way. The granted scopes go on request.state.scopes for require_scope().


async def _count_usage(api_key: ApiKey, db: AsyncSession) -> None:
    if api_key.tier == "free":
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        usage = (await db.execute(select(ApiUsageCounter).where(ApiUsageCounter.api_key_id == api_key.id, ApiUsageCounter.day == day))).scalar_one_or_none()
        if usage and usage.count >= get_settings().default_rate_limit_free:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Daily request limit reached")
        if not usage:
            usage = ApiUsageCounter(api_key_id=api_key.id, day=day, count=0)
            db.add(usage)
        usage.count += 1


async def require_api_key(request: Request, db: AsyncSession = Depends(get_db)) -> ApiKey:
    raw_key = request.headers.get("X-API-Key")
    authz = request.headers.get("Authorization", "")
    if not raw_key and authz.lower().startswith("bearer "):
        return await _from_bearer(request, authz[7:].strip(), db)
    if not raw_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="X-API-Key is required",
                            headers={"WWW-Authenticate": 'Bearer realm="supplywatch"'})

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    result = await db.execute(select(ApiKey).where(ApiKey.key_hash == key_hash))
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    await _count_usage(api_key, db)
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    from oauth.tokens import API_KEY_SCOPES

    request.state.auth_method = "api_key"
    request.state.scopes = set(API_KEY_SCOPES)
    return api_key


async def _from_bearer(request: Request, token: str, db: AsyncSession) -> ApiKey:
    from oauth.models import OAuthClient
    from oauth.tokens import TokenError, decode

    def deny(desc: str):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=desc,
                             headers={"WWW-Authenticate": f'Bearer error="invalid_token", error_description="{desc}"'})

    try:
        claims = decode(token)
    except TokenError as e:
        raise deny(str(e))
    client = (await db.execute(select(OAuthClient).where(OAuthClient.client_id == claims["sub"]))).scalar_one_or_none()
    if not client or client.revoked:
        raise deny("client revoked")
    api_key = await db.get(ApiKey, client.api_key_id)
    if not api_key:
        raise deny("owner key no longer exists")
    await _count_usage(api_key, db)
    api_key.last_used_at = datetime.now(timezone.utc)
    await db.commit()
    request.state.auth_method = "oauth"
    request.state.scopes = set(claims.get("scope", "").split())
    request.state.client_id = client.client_id
    return api_key


def require_scope(scope: str):
    """Dependency factory: authenticate (API key or bearer) and require `scope`."""

    async def _dep(request: Request, api_key: ApiKey = Depends(require_api_key)) -> ApiKey:
        if scope not in getattr(request.state, "scopes", set()):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"missing scope: {scope}",
                                headers={"WWW-Authenticate": f'Bearer error="insufficient_scope", scope="{scope}"'})
        return api_key

    _dep.__name__ = f"require_scope_{scope.replace(':', '_')}"
    return _dep
