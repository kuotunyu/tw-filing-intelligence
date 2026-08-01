"""Retrieval index: dense vectors, BM25, and the rank fusion that combines them.

Three pieces, shaped by the factor ladder rather than by any general notion of a search
stack. F0 and F1 retrieve with BM25 alone; F2 adds dense retrieval and fuses the two with
RRF. So the lexical and dense halves stay independently usable and neither blends the other
in: ``bge-m3``'s sparse head is deliberately unused, because computing lexical scores with
the same model that computes the dense ones would confound the comparison F2 exists to make.
"""

from twfi.index.dense import search
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
from twfi.index.fusion import DEFAULT_K, RrfConfig, reciprocal_rank_fusion
from twfi.index.lexical import (
    TOKENISER_ID,
    Bm25Config,
    Bm25Index,
    Bm25Manifest,
    load_index,
    save_index,
    tokenise,
)
from twfi.index.retrieve import Hit, Mode, Retriever, recall_at_k

__all__ = [
    # dense
    "DEFAULT_MODEL",
    "EmbeddingConfig",
    "EmbeddingManifest",
    "batches",
    "embed_texts",
    "load_vectors",
    "save_vectors",
    "search",
    "utc_now",
    # lexical
    "TOKENISER_ID",
    "Bm25Config",
    "Bm25Index",
    "Bm25Manifest",
    "load_index",
    "save_index",
    "tokenise",
    # end to end
    "Hit",
    "Mode",
    "Retriever",
    "recall_at_k",
    # fusion
    "DEFAULT_K",
    "RrfConfig",
    "reciprocal_rank_fusion",
]
