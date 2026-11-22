import asyncio
import hashlib
import secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy import select, text

from api.auth import require_api_key
from api.routes.alerts import router as alerts_router
from api.routes.ask import router as ask_router
from api.routes.copilot import router as copilot_router
from api.routes.webhooks import router as webhooks_router
from oauth.routes import router as oauth_router
from api.routes.guest import router as guest_router
from api.routes.health import router as health_router
from api.routes.materials import router as materials_router
from api.schemas import CreateApiKeyRequest
from core.config import get_settings
from core.database import ApiKey, AsyncSessionLocal, Base, Material, engine
from scheduler.jobs import build_scheduler
import rag.models  # noqa: F401  (registers RAG tables on Base)
import webhooks.models  # noqa: F401
import oauth.models  # noqa: F401

# Copilot change: the plan's 10 critical minerals replace the source's 8-item
# list (Yttrium/Dysprosium/Indium -> grouped as "Rare Earths" plus nickel,
# tungsten, antimony, manganese). Same mechanism as the source: inserted on
# startup if missing. History is added separately by scripts/seed.py.
SEED_MATERIALS = [
    ("Lithium", "Li", "Battery Metal", ["Australia", "Chile", "China"]),
    ("Cobalt", "Co", "Battery Metal", ["DRC", "Indonesia"]),
    ("Nickel", "Ni", "Battery Metal", ["Indonesia", "Philippines"]),
    ("Rare Earths", "REE", "Rare Earth", ["China", "Myanmar", "Australia"]),
    ("Gallium", "Ga", "Critical Metal", ["China", "Russia"]),
    ("Germanium", "Ge", "Critical Metal", ["China", "Russia"]),
    ("Graphite", "C", "Battery Material", ["China", "Madagascar", "Mozambique"]),
    ("Tungsten", "W", "Critical Metal", ["China", "Vietnam"]),
    ("Antimony", "Sb", "Critical Metal", ["China", "Tajikistan", "Russia"]),
    ("Manganese", "Mn", "Battery Metal", ["South Africa", "Gabon", "Australia"]),
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as db:
        for n, s, c, p in SEED_MATERIALS:
            exists = (await db.execute(select(Material).where(Material.name == n))).scalar_one_or_none()
            if not exists:
                db.add(Material(name=n, symbol=s, category=c, primary_producers=p))
        await db.commit()
    # Copilot: load the embedding model once at startup so the first /ask isn't slow.
    from rag.embeddings import embed_query
    await asyncio.to_thread(embed_query, "warmup")
    scheduler = build_scheduler(AsyncSessionLocal)
    if get_settings().enable_scheduler:
        scheduler.start()
    yield
    if scheduler.running:  # copilot fix: source crashed on shutdown when the scheduler was disabled
        scheduler.shutdown(wait=False)

app = FastAPI(title="SupplyWatch", version=get_settings().app_version, lifespan=lifespan)
app.include_router(health_router)
app.include_router(materials_router)
app.include_router(alerts_router)
app.include_router(guest_router)
app.include_router(ask_router)
app.include_router(copilot_router)
app.include_router(webhooks_router)
app.include_router(oauth_router)

@app.post('/auth/keys')
async def create_key(payload: CreateApiKeyRequest):
    raw_key = f"sw_{secrets.token_urlsafe(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        db.add(ApiKey(key_hash=key_hash, company_name=payload.company_name, tier=payload.tier))
        await db.commit()
    return {"data": {"api_key": raw_key, "tier": payload.tier}, "meta": {"timestamp": __import__('datetime').datetime.now(__import__('datetime').timezone.utc), "version": get_settings().app_version}}
