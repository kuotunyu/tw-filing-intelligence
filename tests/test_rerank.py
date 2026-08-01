"""Cross-encoder reranking, the F3 rung.

No model here: the scorer is injected, so every property below is pinned offline. What they
protect is the thing that makes F3's number mean anything -- that reranking is a *reordering* of
the retrieval output, applied to an identical shortlist, with a deterministic tie-break.
"""

from __future__ import annotations

import pytest

from twfi.index.rerank import Reranker, rerank_hits
from twfi.index.retrieve import Hit

DOC = "1301-FY2023-AR"


def hit(index: int, *, page: int = 1, text: str = "") -> Hit:
    return Hit(
        chunk_index=index,
        score=1.0 / (index + 1),
        chunk_id=f"{DOC}:struct:{index:05d}",
        doc_id=DOC,
        pages=(page,),
        section_path=("壹", "一"),
        text=text or f"chunk {index}",
    )


def scorer(mapping: dict[str, float]) -> object:
    def score(_query: str, passages: list[str]) -> list[float]:
        return [mapping.get(passage, 0.0) for passage in passages]

    return score


def test_reranking_reorders_by_score() -> None:
    hits = [hit(0), hit(1), hit(2)]
    ranked = rerank_hits(
        "q",
        hits,
        score_pairs=scorer({"chunk 0": 0.1, "chunk 1": 0.9, "chunk 2": 0.5}),  # type: ignore[arg-type]
        top_k=3,
    )
    assert [h.chunk_index for h in ranked] == [1, 2, 0]


def test_the_score_is_replaced_not_blended() -> None:
    """A cross-encoder logit and an RRF score are on unrelated scales."""
    ranked = rerank_hits(
        "q",
        [hit(0)],
        score_pairs=scorer({"chunk 0": 7.25}),  # type: ignore[arg-type]
        top_k=1,
    )
    assert ranked[0].score == 7.25


def test_provenance_survives_reranking() -> None:
    """G4 scores citations off these fields; a reranker that dropped them would break it."""
    ranked = rerank_hits("q", [hit(3, page=188)], score_pairs=scorer({}), top_k=1)  # type: ignore[arg-type]
    assert ranked[0].chunk_id == f"{DOC}:struct:00003"
    assert ranked[0].doc_id == DOC
    assert ranked[0].pages == (188,)
    assert ranked[0].section_path == ("壹", "一")


def test_ties_keep_the_retrieval_order() -> None:
    """Protocol 3.2 reads positions off this list, so an unstable tie makes a run irreproducible."""
    hits = [hit(5), hit(2), hit(9)]
    ranked = rerank_hits("q", hits, score_pairs=scorer({}), top_k=3)  # type: ignore[arg-type]
    assert [h.chunk_index for h in ranked] == [5, 2, 9]


def test_the_cutoff_is_applied_after_reordering() -> None:
    """Truncating first would keep whatever retrieval ranked highest, which is not reranking."""
    hits = [hit(0), hit(1), hit(2)]
    ranked = rerank_hits(
        "q",
        hits,
        score_pairs=scorer({"chunk 0": 0.1, "chunk 1": 0.2, "chunk 2": 0.9}),  # type: ignore[arg-type]
        top_k=1,
    )
    assert [h.chunk_index for h in ranked] == [2]


def test_a_score_count_mismatch_is_refused() -> None:
    """Pairing each chunk with another chunk's score reorders into nonsense while looking fine."""

    def short(_query: str, passages: list[str]) -> list[float]:
        return [1.0] * (len(passages) - 1)

    with pytest.raises(ValueError, match="refusing to pair them up"):
        rerank_hits("q", [hit(0), hit(1)], score_pairs=short, top_k=2)  # type: ignore[arg-type]


def test_an_empty_shortlist_reranks_to_nothing() -> None:
    assert rerank_hits("q", [], score_pairs=scorer({}), top_k=5) == []  # type: ignore[arg-type]


def test_a_non_positive_cutoff_is_refused() -> None:
    with pytest.raises(ValueError, match="top_k must be positive"):
        rerank_hits("q", [hit(0)], score_pairs=scorer({}), top_k=0)  # type: ignore[arg-type]


def test_asking_for_more_than_arrived_returns_what_arrived() -> None:
    ranked = rerank_hits("q", [hit(0), hit(1)], score_pairs=scorer({}), top_k=20)  # type: ignore[arg-type]
    assert len(ranked) == 2


def test_the_reranker_object_carries_its_cutoff() -> None:
    reranker = Reranker(score_pairs=scorer({"chunk 1": 1.0}), top_k=1)  # type: ignore[arg-type]
    assert [h.chunk_index for h in reranker.rerank("q", [hit(0), hit(1)])] == [1]


def test_a_reranker_with_a_nonsense_cutoff_cannot_be_built() -> None:
    with pytest.raises(ValueError, match="top_k must be positive"):
        Reranker(score_pairs=scorer({}), top_k=0)  # type: ignore[arg-type]


def test_negative_scores_order_correctly() -> None:
    """Cross-encoder logits are unbounded and frequently negative."""
    ranked = rerank_hits(
        "q",
        [hit(0), hit(1)],
        score_pairs=scorer({"chunk 0": -8.0, "chunk 1": -2.0}),  # type: ignore[arg-type]
        top_k=2,
    )
    assert [h.chunk_index for h in ranked] == [1, 0]
