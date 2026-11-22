"""Seed the database: the 10 materials, 30 days of SYNTHETIC score history,
and (optionally) the RAG corpus.

    python scripts/seed.py            # materials + history + corpus ingest
    python scripts/seed.py --no-corpus

The score history is SYNTHETIC (a seeded random walk around a per-material
baseline), so the API has something to show without hitting the live
yfinance / Google News sources. Every synthetic RawSignal row is tagged
source="synthetic_seed" and raw_data.synthetic = true.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select, text  # noqa: E402

from core.database import AsyncSessionLocal, Base, DisruptionScore, Material, RawSignal, engine  # noqa: E402
from main import SEED_MATERIALS  # noqa: E402

# Baseline risk per material (0-100) for the synthetic walk. Loosely ordered
# by how concentrated/export-restricted supply was in 2024-25 public reporting.
BASELINES = {
    "Gallium": 74, "Germanium": 70, "Antimony": 72, "Rare Earths": 66, "Graphite": 61,
    "Tungsten": 57, "Cobalt": 52, "Lithium": 41, "Nickel": 36, "Manganese": 27,
}
POINTS = 30 * 4  # every 6 hours for 30 days, matching the source scheduler cadence


async def seed_materials_and_history() -> int:
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    n_rows = 0
    async with AsyncSessionLocal() as db:
        for name, symbol, category, producers in SEED_MATERIALS:
            m = (await db.execute(select(Material).where(Material.name == name))).scalar_one_or_none()
            if not m:
                m = Material(name=name, symbol=symbol, category=category, primary_producers=producers)
                db.add(m)
                await db.flush()
            # idempotent: wipe previous synthetic rows for this material
            await db.execute(delete(DisruptionScore).where(DisruptionScore.material_id == m.id))
            await db.execute(delete(RawSignal).where(RawSignal.material_id == m.id, RawSignal.source == "synthetic_seed"))
            rng = random.Random(f"seed-{name}")
            base = BASELINES.get(name, 40)
            score = base
            for i in range(POINTS):
                ts = now - timedelta(hours=6 * (POINTS - 1 - i))
                score = max(0, min(100, round(score + rng.gauss(0, 2.0) + (base - score) * 0.15)))
                price = max(0, min(100, score + rng.randint(-15, 10)))
                export = max(0, min(100, score + rng.randint(-10, 20)))
                trade = max(0, min(100, score + rng.randint(-10, 10)))
                db.add(DisruptionScore(material_id=m.id, score=score,
                                       factors={"price": price, "export": export, "trade": trade},
                                       computed_at=ts))
                n_rows += 1
            db.add(RawSignal(material_id=m.id, source="synthetic_seed",
                             raw_data={"synthetic": True, "note": "seeded random-walk history, not live data",
                                       "baseline": base}))
        await db.commit()
    return n_rows


async def main(with_corpus: bool) -> None:
    n = await seed_materials_and_history()
    print(f"seeded {len(SEED_MATERIALS)} materials, {n} synthetic score rows")
    if with_corpus:
        from rag.ingest import ingest_corpus_dir
        stats = await ingest_corpus_dir()
        print(f"ingested corpus: {stats}")
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-corpus", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(not args.no_corpus))
