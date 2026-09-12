"""Vectors for retrieval.

A deliberate choice worth stating: the default embedder is a *local, deterministic*
hashed tf-idf, not a hosted embedding model. Three reasons.

1. There is no first-party embedding endpoint alongside the models this engine
   drafts with, so a hosted embedder means a second vendor and a second failure
   mode in the middle of the retrieval path.
2. Ingestion must be re-runnable and reproducible. An embedder whose output can
   change under you invalidates the index silently.
3. In this pipeline, vectors do second-pass work. Retrieval is filtered first by
   hard structure — POV, situation and technique tags, date — and vectors only
   rank what survives. Lexical similarity is enough for that, and the backtest
   ablations can say otherwise if it isn't.

``set_embedder`` replaces it with anything better, and re-running ``bp ingest``
rebuilds the index.
"""

from __future__ import annotations

import array
import hashlib
import math
import re
from typing import Callable, Sequence

DIMS = 256
_WORD = re.compile(r"[A-Za-z']+")


def _hash_dim(token: str) -> int:
    return int.from_bytes(hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest(), "big") % DIMS


def hashed_tfidf(text: str, *, dims: int = DIMS) -> list[float]:
    """Sublinear term frequency projected onto a fixed hashed basis, L2-normalised."""
    counts: dict[str, int] = {}
    for w in _WORD.findall(text.lower()):
        if len(w) < 3:
            continue
        counts[w] = counts.get(w, 0) + 1
    vec = [0.0] * dims
    for token, n in counts.items():
        vec[_hash_dim(token)] += 1.0 + math.log(n)
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / norm for x in vec] if norm else vec


_embedder: Callable[[str], Sequence[float]] = hashed_tfidf


def set_embedder(fn: Callable[[str], Sequence[float]]) -> None:
    """Swap in a real embedding model. Re-run ingestion afterwards."""
    global _embedder
    _embedder = fn


def embed(text: str) -> list[float]:
    return list(_embedder(text))


def pack(vec: Sequence[float]) -> bytes:
    return array.array("f", vec).tobytes()


def unpack(blob: bytes | None) -> list[float]:
    if not blob:
        return []
    a = array.array("f")
    a.frombytes(blob)
    return list(a)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
