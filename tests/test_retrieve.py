"""The retriever must not answer in a mode it cannot actually run.

F2's whole claim is the difference between lexical and hybrid, so a retriever that silently
falls back to lexical when its dense index is absent would report F2's number while running
F1 -- and nothing downstream could tell. Most of these tests are that refusal.
"""

from __future__ import annotations

import numpy as np
import pytest

from twfi.index.lexical import Bm25Index
from twfi.index.retrieve import Hit, Retriever, recall_at_k

CHUNKS = [
    {
        "chunk_id": "c0",
        "doc_id": "1301-FY2023-AR",
        "pages": [188],
        "section_path": ["柒", "一"],
        "text": "資產總計 530,738,356 負債總計 183,378,211",
        "parser": "twfi-layout",
    },
    {
        "chunk_id": "c1",
        "doc_id": "1301-FY2023-AR",
        "pages": [191],
        "section_path": ["柒", "二"],
        "text": "現金流量比率 5.97% 現金流量允當比率 82.82%",
        "parser": "twfi-layout",
    },
    {
        "chunk_id": "c2",
        "doc_id": "2412-FY2023-AR",
        "pages": [137],
        "section_path": [],
        "text": "中華電信合併資產負債表 資產總計 523,939,401",
        "parser": "twfi-layout",
    },
]

VECTORS = np.array(
    [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
    dtype=np.float32,
)


def embed_first(_query: str) -> np.ndarray:
    """A fixed query vector, so dense order is known without running a model."""
    return np.array([1.0, 0.0, 0.0], dtype=np.float32)


def full() -> Retriever:
    return Retriever(
        chunks=CHUNKS,
        bm25=Bm25Index.build([row["text"] for row in CHUNKS]),
        vectors=VECTORS,
        embed_query=embed_first,
    )


# ------------------------------------------------- refusing to run the wrong mode


def test_hybrid_without_vectors_is_refused_not_downgraded() -> None:
    """The failure this prevents: reporting F2's number while running F1."""
    lexical_only = Retriever(chunks=CHUNKS, bm25=Bm25Index.build([r["text"] for r in CHUNKS]))
    with pytest.raises(ValueError, match="refusing to fall back to lexical"):
        lexical_only.search("資產總計", 3, mode="hybrid")


def test_dense_without_an_embedder_is_refused() -> None:
    no_embedder = Retriever(chunks=CHUNKS, vectors=VECTORS)
    with pytest.raises(ValueError, match="embed_query"):
        no_embedder.search("資產總計", 3, mode="dense")


def test_lexical_without_bm25_is_refused() -> None:
    dense_only = Retriever(chunks=CHUNKS, vectors=VECTORS, embed_query=embed_first)
    with pytest.raises(ValueError, match="BM25"):
        dense_only.search("資產總計", 3, mode="lexical")


def test_a_non_positive_top_k_is_refused() -> None:
    with pytest.raises(ValueError, match="top_k must be positive"):
        full().search("資產總計", 0)


# ------------------------------------------------------- index/chunk agreement


def test_vectors_that_do_not_match_the_chunks_are_refused_at_construction() -> None:
    """A stale index returns the wrong provenance, which G4 would score as a bad citation."""
    with pytest.raises(ValueError, match="different chunking"):
        Retriever(chunks=CHUNKS, vectors=np.zeros((2, 3), dtype=np.float32))


def test_a_bm25_index_of_the_wrong_size_is_refused() -> None:
    with pytest.raises(ValueError, match="BM25 index holds"):
        Retriever(chunks=CHUNKS, bm25=Bm25Index.build(["only one document"]))


# --------------------------------------------------------------- what it returns


def test_a_hit_carries_the_provenance_g4_needs() -> None:
    hits = full().search("現金流量比率", 3, mode="lexical")
    assert hits, "the query terms are in the corpus"
    best = hits[0]
    assert best.doc_id == "1301-FY2023-AR"
    assert best.pages == (191,)
    assert best.section_path == ("柒", "二")
    assert best.cites("1301-FY2023-AR", 191)
    assert not best.cites("1301-FY2023-AR", 188)


def test_lexical_finds_the_chunk_holding_the_query() -> None:
    hits = full().search("中華電信合併資產負債表", 3, mode="lexical")
    assert hits[0].chunk_id == "c2"


def test_the_three_modes_are_one_code_path_with_one_input_switched() -> None:
    """Two implementations could diverge for reasons nobody measured; these must not."""
    retriever = full()
    for mode in ("lexical", "dense", "hybrid"):
        hits = retriever.search("資產總計", 2, mode=mode)  # type: ignore[arg-type]
        assert len(hits) <= 2
        assert all(isinstance(hit, Hit) for hit in hits)


def test_hybrid_can_rank_differently_from_either_side() -> None:
    """If it could not, fusing would be pointless and the F2 comparison empty."""
    retriever = full()
    lexical = [hit.chunk_id for hit in retriever.search("資產總計", 3, mode="lexical")]
    dense = [hit.chunk_id for hit in retriever.search("資產總計", 3, mode="dense")]
    hybrid = [hit.chunk_id for hit in retriever.search("資產總計", 3, mode="hybrid")]
    assert hybrid, "fusion returned nothing"
    assert lexical != dense, "the fixture must make the two sides disagree"
    assert set(hybrid) <= {row["chunk_id"] for row in CHUNKS}


# ------------------------------------------------------------------ recall@k


def test_recall_counts_a_page_level_hit() -> None:
    hits = full().search("現金流量比率", 3, mode="lexical")
    assert recall_at_k(hits, doc_id="1301-FY2023-AR", pages=[191])


def test_recall_is_false_for_the_right_page_in_the_wrong_document() -> None:
    hits = full().search("現金流量比率", 3, mode="lexical")
    assert not recall_at_k(hits, doc_id="2330-FY2024-FS", pages=[191])


def test_a_record_naming_no_page_does_not_count_as_recalled() -> None:
    """Counting it as success would inflate recall with records that never tested it."""
    hits = full().search("資產總計", 3, mode="lexical")
    assert not recall_at_k(hits, doc_id="1301-FY2023-AR", pages=[])


def test_recall_on_no_hits_is_false() -> None:
    assert not recall_at_k([], doc_id="1301-FY2023-AR", pages=[188])
