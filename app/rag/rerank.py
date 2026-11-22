"""Cross-encoder reranking (cross-encoder/ms-marco-MiniLM-L-6-v2, ~22M params, CPU)."""
from __future__ import annotations

import os
import threading
from functools import lru_cache

RERANK_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_CANDIDATES = int(os.environ.get("RERANK_CANDIDATES", "10"))
RERANK_MAX_TOKENS = int(os.environ.get("RERANK_MAX_TOKENS", "320"))
_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_reranker():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(RERANK_MODEL, device="cpu", max_length=RERANK_MAX_TOKENS)


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    with _lock:
        return [float(s) for s in get_reranker().predict([(query, p) for p in passages], batch_size=16,
                                                          show_progress_bar=False)]
