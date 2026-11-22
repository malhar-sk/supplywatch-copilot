"""Short-lived HS256 JWT access tokens for the client-credentials grant."""
from __future__ import annotations

import time
import uuid

import jwt

from core.config import get_settings

ISSUER = "supplywatch"
AUDIENCE = "supplywatch-api"
ALL_SCOPES = ("read:materials", "ask", "admin")
API_KEY_SCOPES = ("read:materials", "ask")  # what a plain X-API-Key is allowed to do


class TokenError(Exception):
    pass


def issue(client_id: str, api_key_id: int, scopes: list[str], ttl: int | None = None, now: float | None = None) -> tuple[str, int]:
    s = get_settings()
    ttl = ttl or s.jwt_ttl_seconds
    now = int(now or time.time())
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": client_id, "akid": api_key_id, "scope": " ".join(scopes),
              "iat": now, "nbf": now, "exp": now + ttl, "jti": uuid.uuid4().hex}
    return jwt.encode(claims, s.jwt_secret, algorithm="HS256"), ttl


def decode(token: str) -> dict:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER,
                          options={"require": ["exp", "iat", "sub", "aud", "iss"]})
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token expired") from e
    except jwt.InvalidTokenError as e:
        raise TokenError(f"invalid token: {e}") from e
