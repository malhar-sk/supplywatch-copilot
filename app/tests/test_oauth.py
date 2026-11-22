"""M7: OAuth2 client credentials -> short-lived JWT; API key OR bearer on protected routes;
expiry and scope denial."""
import base64
import os
import time

import pytest

from tests.helpers import make_client, new_api_key, seed_db

ADMIN = "test-admin-bootstrap-secret"


@pytest.fixture(scope="module")
def client():
    os.environ["ADMIN_BOOTSTRAP_SECRET"] = ADMIN
    from core.config import get_settings

    get_settings.cache_clear()
    seed_db()
    with make_client() as c:
        yield c
    get_settings.cache_clear()


def _new_oauth_client(c, scopes, admin_secret=None):
    key = new_api_key(c)
    h = {"X-API-Key": key}
    if admin_secret:
        h["X-Admin-Secret"] = admin_secret
    return c.post("/oauth/clients", headers=h, json={"name": "t", "scopes": scopes}), key


def _token(c, cid, secret, scope=None):
    data = {"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}
    if scope:
        data["scope"] = scope
    return c.post("/oauth/token", data=data)


def test_client_credentials_flow_and_bearer_access(client):
    r, _ = _new_oauth_client(client, ["read:materials", "ask"])
    cred = r.json()["data"]
    t = _token(client, cred["client_id"], cred["client_secret"])
    assert t.status_code == 200 and t.headers["cache-control"] == "no-store"
    body = t.json()
    assert body["token_type"] == "Bearer" and 0 < body["expires_in"] <= 3600 and body["scope"] == "read:materials ask"
    bearer = {"Authorization": f"Bearer {body['access_token']}"}
    assert client.get("/materials", headers=bearer).status_code == 200
    assert client.get("/tools/signal", params={"material": "Nickel"}, headers=bearer).status_code == 200
    assert client.post("/ask", headers=bearer, json={"question": "When did China ban gallium exports?"}).status_code == 200


def test_scope_denial(client):
    r, _ = _new_oauth_client(client, ["read:materials"])
    cred = r.json()["data"]
    tok = _token(client, cred["client_id"], cred["client_secret"]).json()["access_token"]
    bearer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/materials", headers=bearer).status_code == 200
    denied = client.post("/ask", headers=bearer, json={"question": "anything about gallium?"})
    assert denied.status_code == 403 and "missing scope: ask" in denied.json()["detail"]
    assert 'insufficient_scope' in denied.headers["www-authenticate"]
    # narrowing works, widening is refused
    assert _token(client, cred["client_id"], cred["client_secret"], scope="ask").json()["error"] == "invalid_scope"


def test_expired_and_tampered_tokens_are_rejected(client):
    from oauth.tokens import issue

    r, _ = _new_oauth_client(client, ["read:materials"])
    cid = r.json()["data"]["client_id"]
    from sqlalchemy import select  # resolve owner id via the API: token for this client, issued an hour ago
    import asyncio
    from core.database import AsyncSessionLocal
    from oauth.models import OAuthClient

    async def owner():
        async with AsyncSessionLocal() as db:
            return (await db.execute(select(OAuthClient).where(OAuthClient.client_id == cid))).scalar_one().api_key_id

    old, _ = issue(cid, asyncio.run(owner()), ["read:materials"], ttl=60, now=time.time() - 3600)
    resp = client.get("/materials", headers={"Authorization": f"Bearer {old}"})
    assert resp.status_code == 401 and resp.json()["detail"] == "token expired"
    fresh, _ = issue(cid, asyncio.run(owner()), ["read:materials"])
    head, payload, sig = fresh.split(".")
    tampered = f"{head}.{payload}.{sig[:-2]}{'A' if sig[-2] != 'A' else 'B'}{sig[-1]}"
    assert client.get("/materials", headers={"Authorization": f"Bearer {tampered}"}).status_code == 401


def test_token_endpoint_errors_and_basic_auth(client):
    r, _ = _new_oauth_client(client, ["ask"])
    cred = r.json()["data"]
    assert _token(client, cred["client_id"], "wrong").status_code == 401
    assert _token(client, cred["client_id"], "wrong").json()["error"] == "invalid_client"
    bad_grant = client.post("/oauth/token", data={"grant_type": "password", "client_id": cred["client_id"]})
    assert bad_grant.status_code == 400 and bad_grant.json()["error"] == "unsupported_grant_type"
    basic = base64.b64encode(f"{cred['client_id']}:{cred['client_secret']}".encode()).decode()
    ok = client.post("/oauth/token", data={"grant_type": "client_credentials"}, headers={"Authorization": f"Basic {basic}"})
    assert ok.status_code == 200 and ok.json()["scope"] == "ask"


def test_admin_scope_is_gated(client):
    denied, _ = _new_oauth_client(client, ["admin"])
    assert denied.status_code == 403
    r, key = _new_oauth_client(client, ["admin", "read:materials"], admin_secret=ADMIN)
    assert r.status_code == 200
    cred = r.json()["data"]
    tok = _token(client, cred["client_id"], cred["client_secret"]).json()["access_token"]
    mid = client.get("/materials", headers={"X-API-Key": key}).json()["data"][0]["id"]
    # a plain API key cannot use admin routes; an admin bearer can
    assert client.post(f"/admin/materials/{mid}/score", headers={"X-API-Key": key}, json={"score": 10}).status_code == 403
    ok = client.post(f"/admin/materials/{mid}/score", headers={"Authorization": f"Bearer {tok}"}, json={"score": 10})
    assert ok.status_code == 200 and ok.json()["data"]["score"] == 10


def test_api_key_still_works_everywhere(client):
    key = new_api_key(client)
    h = {"X-API-Key": key}
    assert client.get("/materials", headers=h).status_code == 200
    assert client.post("/ask", headers=h, json={"question": "When did China ban gallium exports?"}).status_code == 200
    assert client.get("/materials").status_code == 401
