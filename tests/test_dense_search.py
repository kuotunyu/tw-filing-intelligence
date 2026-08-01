"""A search that returns an empty list is indistinguishable from a corpus with no answer.

Most of what follows is about refusal: the inputs dense search will not accept, because each of
them otherwise produces a hit list that *looks* like an honest retrieval outcome. No GPU, no
model and no PDF -- the matrix is a handful of rows written out by hand, which is enough to pin
every ordering and every refusal.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pytest

from twfi.index.dense import search

# Four rows in three dimensions, L2-normalised the way embed_texts() writes them. Rows 1 and 2
# are orthogonal to row 0, so a query along row 0 scores them identically -- that is the tie.
CORPUS: Sequence[Sequence[float]] = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
    (1.0, 1.0, 0.0),
)


def corpus(dtype: object = np.float32) -> np.ndarray:
    matrix = np.asarray(CORPUS, dtype=dtype)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return np.asarray(matrix / norms, dtype=dtype)


def query(*values: float, dtype: object = np.float32) -> np.ndarray:
    return np.asarray(values, dtype=dtype)


# --------------------------------------------------------------- refusing bad shapes


def test_a_query_encoded_by_a_different_model_is_refused() -> None:
    """768 against 1024 is the real case: bge-m3's index searched with another encoder."""
    with pytest.raises(ValueError, match="different model than the index"):
        search(corpus(), query(1.0, 0.0), 2)


def test_a_matrix_that_is_not_two_dimensional_is_refused() -> None:
    with pytest.raises(ValueError, match=r"must be 2-D \[n_chunks, dimension\]"):
        search(query(1.0, 0.0, 0.0), query(1.0, 0.0, 0.0), 1)


def test_a_three_dimensional_matrix_is_refused() -> None:
    with pytest.raises(ValueError, match="must be 2-D"):
        search(np.ones((2, 2, 3), dtype=np.float32), query(1.0, 0.0, 0.0), 1)


def test_several_queries_at_once_are_refused_rather_than_silently_collapsed() -> None:
    """Two stacked queries have no single answer; averaging or taking the first would be a guess."""
    with pytest.raises(ValueError, match="once per query"):
        search(corpus(), np.ones((2, 3), dtype=np.float32), 2)


def test_a_single_row_query_matrix_is_accepted() -> None:
    """embed_texts() returns [1, d] for one text, and one row is unambiguous."""
    hits = search(corpus(), np.asarray([[1.0, 0.0, 0.0]], dtype=np.float32), 1)
    assert hits[0][0] == 0


def test_an_integer_matrix_is_refused() -> None:
    """Not a vector set this package wrote; int dot products would still look like scores."""
    with pytest.raises(ValueError, match="must hold floats"):
        search(np.ones((3, 3), dtype=np.int64), query(1.0, 0.0, 0.0), 2)


# ------------------------------------------------- refusing what would read as a miss


@pytest.mark.parametrize("top_k", [0, -1, -20])
def test_a_non_positive_top_k_is_refused_rather_than_reported_as_no_hits(top_k: int) -> None:
    with pytest.raises(ValueError, match="top_k must be positive"):
        search(corpus(), query(1.0, 0.0, 0.0), top_k)


def test_an_empty_index_is_refused_rather_than_returning_no_hits() -> None:
    """Zero rows means the index was never built; an empty result would be filed as a miss."""
    with pytest.raises(ValueError, match="no rows"):
        search(np.empty((0, 3), dtype=np.float32), query(1.0, 0.0, 0.0), 5)


def test_a_zero_query_vector_is_refused_because_it_has_no_direction() -> None:
    """Every score would be 0.0, so the ranking would be corpus order wearing a score column."""
    with pytest.raises(ValueError, match="no direction"):
        search(corpus(), query(0.0, 0.0, 0.0), 2)


@pytest.mark.parametrize("bad", [np.nan, np.inf, -np.inf])
def test_a_non_finite_query_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="NaN or inf"):
        search(corpus(), query(1.0, bad, 0.0), 2)


def test_a_non_finite_index_is_refused_naming_how_many_rows_are_unusable() -> None:
    """NaN sorts arbitrarily, so the result would be a ranking of nothing."""
    matrix = corpus()
    matrix[2, 0] = np.nan
    with pytest.raises(ValueError, match="1 of 4 scores are not finite"):
        search(matrix, query(1.0, 0.0, 0.0), 4)


# ------------------------------------------------------------------------- ordering


def test_the_nearest_row_ranks_first() -> None:
    hits = search(corpus(), query(1.0, 0.0, 0.0), 4)
    assert [row for row, _ in hits] == [0, 3, 1, 2]


def test_scores_are_cosine_similarities() -> None:
    """The matrix is normalised at build time, so an identical query scores exactly 1.0."""
    hits = search(corpus(), query(1.0, 0.0, 0.0), 2)
    assert hits[0][1] == pytest.approx(1.0)
    assert hits[1][1] == pytest.approx(0.5**0.5)


def test_ordering_is_descending_including_negative_similarities() -> None:
    hits = search(corpus(), query(-1.0, 0.0, 0.0), 4)
    assert [row for row, _ in hits] == [1, 2, 3, 0]
    assert [score for _, score in hits] == sorted((score for _, score in hits), reverse=True)


def test_ties_are_broken_by_ascending_row_index() -> None:
    """Rows 1 and 2 both score 0.0 here; MRR@10 would move if the tie broke by dict order."""
    hits = search(corpus(), query(1.0, 0.0, 0.0), 4)
    assert (hits[2][0], hits[3][0]) == (1, 2)
    assert hits[2][1] == hits[3][1]


def test_an_unnormalised_query_gives_the_same_order_and_the_same_cosine_scores() -> None:
    """Scaling a query cannot move the ranking, but it would change the number we record."""
    scaled = search(corpus(), query(7.0, 0.0, 0.0), 4)
    unit = search(corpus(), query(1.0, 0.0, 0.0), 4)
    assert scaled == unit
    assert scaled[0][1] == pytest.approx(1.0), "a raw query must not be reported as 7.0"


def test_the_index_is_not_modified_by_a_search() -> None:
    """The matrix is normalised once at build time; a search that touched it would be a copy."""
    matrix = corpus()
    before = matrix.copy()
    search(matrix, query(1.0, 2.0, 0.5), 3)
    assert np.array_equal(matrix, before)


def test_a_float64_index_ranks_the_same_as_a_float32_one() -> None:
    wide = search(corpus(dtype=np.float64), query(1.0, 0.5, 0.0, dtype=np.float64), 4)
    narrow = search(corpus(), query(1.0, 0.5, 0.0), 4)
    assert [row for row, _ in wide] == [row for row, _ in narrow]


# ----------------------------------------------------------------------- top_k size


def test_top_k_returns_exactly_that_many() -> None:
    assert len(search(corpus(), query(1.0, 0.0, 0.0), 2)) == 2


def test_top_k_above_the_corpus_size_returns_everything_rather_than_raising() -> None:
    """top_k_retrieve=20 is frozen by protocol 2.5 and must not fail on a small index."""
    assert len(search(corpus(), query(1.0, 0.0, 0.0), 20)) == 4


def test_hits_are_plain_python_numbers() -> None:
    """These go into run artifacts as JSON; numpy scalars would not serialise."""
    row, score = search(corpus(), query(1.0, 0.0, 0.0), 1)[0]
    assert type(row) is int
    assert type(score) is float
