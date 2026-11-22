import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Copilot additions: tests never call a real LLM and never write the LLM cache.
os.environ.setdefault("LLM_BACKEND", "fake")
os.environ["LLM_CACHE_DIR"] = ""
os.environ.setdefault("DB_NULLPOOL", "1")
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:15432/supplywatch")
