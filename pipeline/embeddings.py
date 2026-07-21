"""Embedding provider seam (Voyage AI). Kept deliberately tiny so tests stub
it and a provider swap touches one file. Without VOYAGE_API_KEY the rest of
the system never enqueues embed/dedup work — see worker + cli.scan."""

from __future__ import annotations

import httpx

from . import config

_URL = "https://api.voyageai.com/v1/embeddings"


def available() -> bool:
    return bool(config.VOYAGE_API_KEY)


def embed(texts: list[str]) -> list[list[float]]:
    if not available():
        raise RuntimeError("VOYAGE_API_KEY is not set")
    r = httpx.post(
        _URL,
        headers={"Authorization": f"Bearer {config.VOYAGE_API_KEY}"},
        json={"input": [t[:config.EMBED_MAX_CHARS] for t in texts],
              "model": config.EMBED_MODEL,
              "output_dimension": config.EMBED_DIM},
        timeout=60,
    )
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d["index"])
    return [d["embedding"] for d in data]


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6g}" for x in vec) + "]"
