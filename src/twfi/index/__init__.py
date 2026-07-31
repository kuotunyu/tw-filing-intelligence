"""Retrieval index: dense vectors now, BM25 and fusion to follow."""

from twfi.index.embeddings import (
    DEFAULT_MODEL,
    EmbeddingConfig,
    EmbeddingManifest,
    batches,
    embed_texts,
    load_vectors,
    save_vectors,
    utc_now,
)

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingConfig",
    "EmbeddingManifest",
    "batches",
    "embed_texts",
    "load_vectors",
    "save_vectors",
    "utc_now",
]
