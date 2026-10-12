"""Tiny OpenAI-compatible embedding server on CPU (keeps the GPU for the LLM).

Run with the dedicated venv:  $OPS_HOME/venv-embed/bin/python ops/embed_server.py
Endpoints: GET /health, GET /v1/models, POST /v1/embeddings (float or base64).
"""

import base64
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# Cap CPU threads before torch import: unbounded threads + concurrent requests
# oversubscribe the box (thousands of threads, timeouts cascade).
THREADS = int(os.environ.get("EMBED_THREADS", "16"))
for _var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "RAYON_NUM_THREADS"):
    os.environ[_var] = str(THREADS)

import numpy as np  # noqa: E402
import torch  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

torch.set_num_threads(THREADS)

MODEL = os.environ.get("EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
NAME = os.environ.get("EMBED_NAME", "local-embed")
PORT = int(os.environ.get("EMBED_PORT", "8002"))
BATCH = int(os.environ.get("EMBED_BATCH", "16"))
MAX_LEN = int(os.environ.get("EMBED_MAX_LEN", "8192"))

# Big requests encode in slices of MICRO_BATCH texts and re-take the lock per
# slice, so a short request (a search query) waits for one slice, not a whole
# 16-text batch. Requests with <= PRIORITY_MAX_TEXTS texts jump the queue.
MICRO_BATCH = max(1, int(os.environ.get("EMBED_MICRO_BATCH", "4")))
PRIORITY_MAX_TEXTS = int(os.environ.get("EMBED_PRIORITY_MAX_TEXTS", "2"))
# Bounded priority: at most HIGH_BURST short requests in a row while a big
# request's slice waits, then the slice runs (no starvation under steady traffic).
HIGH_BURST = max(1, int(os.environ.get("EMBED_HIGH_BURST", "3")))

model = SentenceTransformer(MODEL, device="cpu")
model.max_seq_length = MAX_LEN


class PriorityLock:
    """One encode at a time (concurrent encodes only fight over the same
    cores). Waiting high-priority callers go first, but bounded: after
    HIGH_BURST high grants in a row a waiting low caller gets the next turn,
    so continuous small traffic cannot starve a big request (B6)."""

    def __init__(self, high_burst: int) -> None:
        self._cond = threading.Condition()
        self._busy = False
        self._waiting_high = 0
        self._waiting_low = 0
        self._high_streak = 0
        self._burst = max(1, high_burst)

    def _may_enter(self, high: bool) -> bool:
        if self._busy:
            return False
        low_turn = self._waiting_low > 0 and self._high_streak >= self._burst
        if high:
            return not low_turn
        return not self._waiting_high or low_turn

    @contextmanager
    def hold(self, high: bool) -> Iterator[None]:
        with self._cond:
            if high:
                self._waiting_high += 1
            else:
                self._waiting_low += 1
            try:
                while not self._may_enter(high):
                    self._cond.wait()
            finally:
                if high:
                    self._waiting_high -= 1
                else:
                    self._waiting_low -= 1
            self._busy = True
            self._high_streak = self._high_streak + 1 if high else 0
        try:
            yield
        finally:
            with self._cond:
                self._busy = False
                self._cond.notify_all()


_lock = PriorityLock(HIGH_BURST)


def encode(texts: list[str]) -> tuple[np.ndarray, int]:
    high = len(texts) <= PRIORITY_MAX_TEXTS
    parts = []
    n_tokens = 0
    for i in range(0, len(texts), MICRO_BATCH):
        part = texts[i : i + MICRO_BATCH]
        with _lock.hold(high):
            parts.append(
                model.encode(
                    part,
                    batch_size=BATCH,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                ).astype(np.float32)
            )
            n_tokens += sum(len(model.tokenizer.encode(t)) for t in part)
    return np.concatenate(parts), n_tokens


app = FastAPI(title="local-embed")


class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    encoding_format: str | None = "float"
    dimensions: int | None = None


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL,
        "threads": THREADS,
        "micro_batch": MICRO_BATCH,
        "priority_max_texts": PRIORITY_MAX_TEXTS,
        "high_burst": HIGH_BURST,
    }


@app.get("/v1/models")
def models() -> dict:
    return {
        "object": "list",
        "data": [{"id": NAME, "object": "model", "owned_by": "local"}],
    }


@app.post("/v1/embeddings")
def embeddings(req: EmbeddingRequest) -> dict:
    texts = [req.input] if isinstance(req.input, str) else req.input
    if not texts:
        raise HTTPException(status_code=400, detail="input is empty")
    vecs, n_tokens = encode(texts)
    if req.dimensions:  # Matryoshka truncation, then re-normalize
        vecs = vecs[:, : req.dimensions]
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-12
    data = []
    for i, v in enumerate(vecs):
        emb = (
            base64.b64encode(v.tobytes()).decode()
            if req.encoding_format == "base64"
            else v.tolist()
        )
        data.append({"object": "embedding", "index": i, "embedding": emb})
    return {
        "object": "list",
        "data": data,
        "model": NAME,
        "usage": {"prompt_tokens": n_tokens, "total_tokens": n_tokens},
    }


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=PORT)
