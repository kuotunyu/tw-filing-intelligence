"""Why F2 fuses ranks and not scores, written as tests rather than as an assurance.

The two demonstrations in the last section are the point of the module: on the same two hit
lists, a weighted sum of raw scores and reciprocal rank fusion disagree about which document is
first, and rescaling one retriever -- something no retrieval metric can see -- flips the score
fusion while leaving rank fusion untouched. That is the free parameter this study cannot afford
to carry inside the F2 - F1 delta.

Everything else here is refusal and determinism. No model, no index, no GPU: fusion sees only
lists of ids.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping

import pytest

from twfi.index.fusion import DEFAULT_K, RrfConfig, reciprocal_rank_fusion

# Two retrievers over the same corpus, with their native score scales: BM25 in the tens,
# cosine in [-1, 1]. Both lists are written best-first.
BM25_SCORES: Mapping[int, float] = {10: 38.0, 20: 37.5, 30: 12.0, 40: 9.0, 50: 4.0}
DENSE_SCORES: Mapping[int, float] = {20: 0.95, 60: 0.80, 30: 0.70, 70: 0.60, 10: 0.55}


def by_score(scored: Mapping[int, float]) -> list[int]:
    """The ranking a retriever hands over: ids best-first, ties by id."""
    return [
        document for document, _ in sorted(scored.items(), key=lambda item: (-item[1], item[0]))
    ]


def weighted_sum(*scored: Mapping[int, float]) -> list[int]:
    """The rejected alternative: add the raw scores, absent meaning zero."""
    totals: dict[int, float] = {}
    for retriever in scored:
        for document, value in retriever.items():
            totals[document] = totals.get(document, 0.0) + value
    return [
        document for document, _ in sorted(totals.items(), key=lambda item: (-item[1], item[0]))
    ]


def fused_ids(*rankings: list[int], config: RrfConfig | None = None) -> list[int]:
    return [document for document, _ in reciprocal_rank_fusion(list(rankings), config)]


# ----------------------------------------------------------------------- the constant


def test_k_is_the_value_the_protocol_froze() -> None:
    """Protocol 2.5 fixed RRF k=60 before any run; a different default would be a fitted one."""
    assert DEFAULT_K == 60
    assert RrfConfig().k == 60


@pytest.mark.parametrize("k", [0, -1, -60])
def test_a_k_below_one_is_refused(k: int) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        RrfConfig(k=k)


def test_the_config_cannot_be_edited_after_construction() -> None:
    config = RrfConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.k = 10  # type: ignore[misc]


# --------------------------------------------------------------------------- refusal


def test_no_rankings_at_all_is_refused_rather_than_reported_as_no_hits() -> None:
    """Zero lists is a wiring error; an empty result would be filed as a retrieval miss."""
    with pytest.raises(ValueError, match="nothing to fuse"):
        reciprocal_rank_fusion([])


@pytest.mark.parametrize("top_k", [0, -1])
def test_a_non_positive_top_k_is_refused(top_k: int) -> None:
    with pytest.raises(ValueError, match="top_k must be positive"):
        reciprocal_rank_fusion([[1, 2]], top_k=top_k)


def test_retrievers_that_all_found_nothing_give_an_honest_empty_result() -> None:
    """Unlike the case above, this is an outcome: both lists exist and both are empty."""
    assert reciprocal_rank_fusion([[], []]) == []


# ------------------------------------------------------------------------- behaviour


def test_the_score_is_the_sum_of_reciprocal_ranks() -> None:
    assert reciprocal_rank_fusion([[7, 8]]) == [(7, 1 / 61), (8, 1 / 62)]


def test_a_document_in_both_lists_outscores_one_that_leads_a_single_list() -> None:
    """This is the whole reason for fusing: agreement beats one retriever's favourite."""
    fused = dict(reciprocal_rank_fusion([[1, 2], [2, 3]]))
    assert fused[2] == pytest.approx(1 / 61 + 1 / 62)
    assert fused[2] > fused[1]


def test_a_document_in_only_one_list_still_scores() -> None:
    fused = dict(reciprocal_rank_fusion([[1, 2], [2, 3]]))
    assert fused[3] == pytest.approx(1 / 62)


def test_one_empty_ranking_among_several_leaves_the_others_untouched() -> None:
    """BM25 can legitimately return nothing when no query term matches the corpus."""
    assert reciprocal_rank_fusion([[], [4, 5]]) == reciprocal_rank_fusion([[4, 5]])


def test_ties_are_broken_by_ascending_document_id() -> None:
    """Symmetric lists score both documents identically; dict order must not decide the ranking."""
    fused = reciprocal_rank_fusion([[1, 2], [2, 1]])
    assert [document for document, _ in fused] == [1, 2]
    assert fused[0][1] == fused[1][1]

    reversed_input = reciprocal_rank_fusion([[2, 1], [1, 2]])
    assert [document for document, _ in reversed_input] == [1, 2]


def test_a_duplicate_id_does_not_let_one_list_vote_twice() -> None:
    """Two votes from one retriever would be a mixing weight, which is what RRF avoids."""
    assert reciprocal_rank_fusion([[7, 7]]) == reciprocal_rank_fusion([[7]])


def test_a_duplicate_does_not_push_the_documents_behind_it_down_a_rank() -> None:
    """Ranking distinct ids keeps an upstream de-duplication bug from rescoring other documents."""
    assert reciprocal_rank_fusion([[7, 7, 3]]) == reciprocal_rank_fusion([[7, 3]])


def test_fusing_the_same_input_twice_gives_the_same_order() -> None:
    rankings = [by_score(BM25_SCORES), by_score(DENSE_SCORES)]
    assert reciprocal_rank_fusion(rankings) == reciprocal_rank_fusion(rankings)


def test_results_are_plain_python_numbers() -> None:
    document, score = reciprocal_rank_fusion([[3]])[0]
    assert type(document) is int
    assert type(score) is float


# ----------------------------------------------------------------------- top_k size


def test_top_k_none_returns_every_document_that_appeared_anywhere() -> None:
    assert len(reciprocal_rank_fusion([[1, 2], [2, 3, 4]])) == 4


def test_top_k_truncates() -> None:
    assert len(reciprocal_rank_fusion([[1, 2], [2, 3, 4]], top_k=2)) == 2


def test_top_k_above_what_exists_returns_everything_rather_than_raising() -> None:
    assert len(reciprocal_rank_fusion([[1, 2]], top_k=20)) == 2


# ------------------------------------------------------- rank fusion vs score fusion


def test_score_fusion_and_rank_fusion_disagree_on_the_same_two_lists() -> None:
    """Document 10 leads BM25 by 0.5 points; document 20 leads dense and is BM25's second.

    Summing raw scores keeps 10 first, because a 0.5-point BM25 lead outweighs the entire
    cosine range. Fusing ranks puts 20 first, because it is ranked 1st and 2nd rather than 1st
    and 5th. The two answers are different, and the difference is decided by nothing more than
    BM25 happening to be measured in tens.
    """
    assert weighted_sum(BM25_SCORES, DENSE_SCORES)[0] == 10
    assert fused_ids(by_score(BM25_SCORES), by_score(DENSE_SCORES))[0] == 20


def test_rescaling_one_retriever_flips_score_fusion_but_not_rank_fusion() -> None:
    """Dividing BM25 by 40 is one arbitrary normalisation among many, and it changes the winner.

    No retrieval metric can see the rescaling -- every document keeps its position in the BM25
    list -- yet score fusion reverses its top two. Whichever scale were chosen, the report would
    be attributing part of the F2 - F1 delta to that choice. Rank fusion cannot be moved by it.
    """
    rescaled = {document: score / 40 for document, score in BM25_SCORES.items()}
    assert by_score(rescaled) == by_score(BM25_SCORES), "rescaling must not change the ranking"

    assert weighted_sum(BM25_SCORES, DENSE_SCORES)[0] == 10
    assert weighted_sum(rescaled, DENSE_SCORES)[0] == 20

    assert fused_ids(by_score(rescaled), by_score(DENSE_SCORES)) == fused_ids(
        by_score(BM25_SCORES), by_score(DENSE_SCORES)
    )


def test_a_small_k_lets_one_top_hit_outvote_agreement() -> None:
    """What k actually does, measured on one pair of lists.

    Document 10 is first in one list and absent from the other; document 40 is fourth in both.
    At the frozen k=60 the damping is strong enough that appearing twice wins. At k=1 a first
    place is worth 0.5 against 0.4 for two fourth places, so the single list decides. The
    comparison is between these two documents rather than for global first place, because the
    other list's own first place also scores 0.5 at k=1 and that tie is a separate property.
    """
    bm25 = [10, 20, 30, 40, 50]
    dense = [60, 70, 80, 40, 90]

    damped = fused_ids(bm25, dense)
    assert damped.index(40) < damped.index(10), "at k=60 agreement outranks one first place"

    sharp = fused_ids(bm25, dense, config=RrfConfig(k=1))
    assert sharp.index(10) < sharp.index(40), "at k=1 one first place outranks agreement"
