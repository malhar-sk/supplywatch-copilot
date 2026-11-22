"""Local embeddings with sentence-transformers (BAAI/bge-small-en-v1.5, 384-d).
No embedding API cost. One model instance per process, 1 CPU thread."""
from __future__ import annotations

import os
import threading
from functools import lru_cache

from core.config import get_settings

# bge v1.5 recommends this instruction for short queries -> passages
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
_lock = threading.Lock()


@lru_cache(maxsize=1)
def get_model():
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(int(os.environ.get("OMP_NUM_THREADS", "1")))
    return SentenceTransformer(get_settings().embedding_model, device="cpu")


def tokenizer():
    return get_model().tokenizer


def embed_passages(texts: list[str]) -> list[list[float]]:
    with _lock:
        vecs = get_model().encode(texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vecs]


def embed_query(text: str) -> list[float]:
    with _lock:
        v = get_model().encode([QUERY_INSTRUCTION + text], normalize_embeddings=True, show_progress_bar=False)[0]
    return v.tolist()
