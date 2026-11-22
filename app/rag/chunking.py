"""Token-aware chunking: ~500 tokens per chunk, 50-token overlap, using the
embedding model's own tokenizer so chunk sizes match what the model sees."""
from __future__ import annotations

import re
from typing import Callable

CHUNK_TOKENS = 500
OVERLAP_TOKENS = 50


def _sentences(text: str) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9(])|\n{2,}", text)
    return [p.strip() for p in parts if p and p.strip()]


def chunk_text(text: str, count_tokens: Callable[[str], int], chunk_tokens: int = CHUNK_TOKENS,
               overlap_tokens: int = OVERLAP_TOKENS) -> list[tuple[str, int]]:
    """Greedy sentence packing. Returns [(chunk_text, n_tokens)]. When a chunk
    closes, trailing sentences totalling ~overlap_tokens are carried into the
    next chunk. A single sentence longer than chunk_tokens becomes its own chunk."""
    sents = [(s, count_tokens(s)) for s in _sentences(text)]
    chunks: list[tuple[str, int]] = []
    cur: list[tuple[str, int]] = []
    cur_tok = 0
    for s, n in sents:
        if cur and cur_tok + n > chunk_tokens:
            chunks.append((" ".join(x for x, _ in cur), cur_tok))
            carry: list[tuple[str, int]] = []
            c = 0
            for x, k in reversed(cur):
                if c + k > overlap_tokens:
                    break
                carry.insert(0, (x, k))
                c += k
            cur, cur_tok = carry, c
        cur.append((s, n))
        cur_tok += n
    if cur:
        chunks.append((" ".join(x for x, _ in cur), cur_tok))
    return chunks
