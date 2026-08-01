"""Dense top-k search: one dot product, and the inputs it refuses to guess at.

The matrix comes from :mod:`twfi.index.embeddings`, which L2-normalises every row at build
time (:attr:`~twfi.index.embeddings.EmbeddingConfig.normalise`). Cosine similarity between
normalised vectors *is* their dot product, so nothing here normalises the matrix: doing it
per query would allocate a second copy of the whole array -- 9,890 x 1024 float32 is ~40 MB
for the candidate parser -- on every one of the 53 questions on every rung of F0-F7, in order
to arrive at the array it already had.

The query is the other case, and it *is* normalised here, because the caller may hand over a
raw encoder output. Scaling a query by a positive constant cannot change the ordering -- every
score moves by the same factor -- but it does change the recorded number, and these scores go
into the run artifacts as cosine similarities. A "similarity" of 12.4 sitting in
``error_analysis`` is not the quantity protocol 3.2 names, and a later reader cannot tell
whether it came from an unnormalised query or from a bug.

What this refuses, and the failure each refusal prevents:

* **A dimension that is not the matrix's.** 768 against 1024 means the query was encoded by a
  different model than the index was built with, or the index belongs to a different build.
  numpy would raise on the shapes alone, but its message names the shapes, not the cause.
* **A matrix that is not 2-D.** A single saved vector, or an array that was transposed on the
  way in, otherwise fails several lines later inside ``argsort`` with an axis error.
* **``top_k <= 0``, and an empty matrix.** Both would return an empty hit list, and an empty
  hit list is a legitimate-looking retrieval outcome: it would be recorded as "nothing
  relevant was retrieved" rather than as a bad call or a missing index.
* **Non-finite values, in the query or in the scores.** NaN sorts arbitrarily, so the result
  would be a plausible-looking ranking that is not a ranking of anything.

``top_k`` above the corpus size is *not* an error -- it clamps. Asking for 20 of 12 chunks is
a well-defined request with 12 answers, and ``top_k_retrieve=20`` is frozen by protocol 2.5;
it must not fail on a small index.
"""

from __future__ import annotations

import numpy as np

__all__ = ["search"]


def search(matrix: np.ndarray, query_vector: np.ndarray, top_k: int) -> list[tuple[int, float]]:
    """Return the ``top_k`` nearest rows as ``(row index, cosine similarity)``.

    ``matrix`` is assumed to be row-normalised already, which is what
    :func:`twfi.index.embeddings.embed_texts` guarantees; see the module docstring for why it
    is not re-normalised here. ``query_vector`` may be raw, or the ``[1, dimension]`` array
    that ``embed_texts`` returns for a single text.

    Ordering is descending by score, ties by ascending row index -- deterministic, because
    Recall@5 and MRR@10 read positions off this list and a tie broken differently between two
    runs would move a reported metric.

    Raises:
        ValueError: If ``top_k`` is not positive, the matrix is not a non-empty 2-D float
            array, the query has the wrong shape or dimension, or either side holds
            non-finite values.
    """
    if top_k <= 0:
        raise ValueError(
            f"top_k must be positive, got {top_k}; a search for no hits is indistinguishable "
            "from a search that found nothing"
        )
    if matrix.ndim != 2:
        raise ValueError(
            f"the index matrix must be 2-D [n_chunks, dimension], got shape {matrix.shape}; "
            "a single vector needs reshape(1, -1) and a transposed array needs .T"
        )
    if not np.issubdtype(matrix.dtype, np.floating):
        raise ValueError(
            f"the index matrix must hold floats, got dtype {matrix.dtype}; this is not a "
            "vector set built by twfi.index.embeddings"
        )
    rows, dimension = int(matrix.shape[0]), int(matrix.shape[1])
    if rows == 0:
        raise ValueError(
            "the index matrix has no rows, so there is nothing to search; rebuild the index "
            "rather than recording an empty result as a retrieval miss"
        )

    query = _prepare_query(query_vector, dimension, matrix.dtype)
    scores = matrix @ query
    if not bool(np.all(np.isfinite(scores))):
        unusable = int(np.count_nonzero(~np.isfinite(scores)))
        raise ValueError(
            f"{unusable} of {rows} scores are not finite, so the index holds NaN or inf; "
            "rebuild it -- sorting non-finite scores yields an arbitrary ranking"
        )

    # Negate and sort ascending-stable rather than sorting and reversing: a reversed stable
    # sort would order ties by *descending* index. Negation of a float is exact, so this
    # cannot perturb the scores. A full argsort over ~10^4 rows costs about a millisecond,
    # which buys the tie guarantee that argpartition would make fiddly to state.
    order = np.argsort(-scores, kind="stable")
    limit = min(top_k, rows)
    # Plain int/float, not numpy scalars: these hits are serialised into run artifacts.
    return [(int(row), float(scores[row])) for row in order[:limit]]


def _prepare_query(query_vector: np.ndarray, dimension: int, matrix_dtype: np.dtype) -> np.ndarray:
    """Validate a query and return it L2-normalised, in a dtype that will not copy the matrix."""
    if query_vector.ndim == 2 and query_vector.shape[0] == 1:
        # embed_texts() returns [1, d] for a single text, and unwrapping one row is
        # unambiguous. [m, d] with m > 1 is m queries and must not be silently collapsed.
        query_vector = query_vector[0]
    if query_vector.ndim != 1:
        raise ValueError(
            f"the query must be one vector of shape [{dimension}], got shape "
            f"{query_vector.shape}; call search() once per query"
        )
    if int(query_vector.shape[0]) != dimension:
        raise ValueError(
            f"query dimension {int(query_vector.shape[0])} does not match the index's "
            f"{dimension}; the query was encoded by a different model than the index"
        )

    query = np.asarray(query_vector, dtype=np.float64)
    if not bool(np.all(np.isfinite(query))):
        raise ValueError(
            "the query vector holds NaN or inf; the encoder failed rather than produced a "
            "weak query, and every score derived from it would be meaningless"
        )
    norm = float(np.linalg.norm(query))
    if norm == 0.0:
        raise ValueError(
            "the query vector is all zeros, so it has no direction: every score would be 0.0 "
            "and the ranking would be nothing but corpus order. Check the encoder output"
        )
    # Match the matrix's dtype so the dot product does not promote -- promotion would copy the
    # whole matrix, the copy this module exists to avoid. float16 is the exception: no index is
    # written in float16, and a 1024-term float16 dot product loses accuracy exactly where the
    # ranking is decided, so such a matrix is promoted deliberately instead.
    compute_dtype = np.float32 if matrix_dtype == np.float16 else matrix_dtype
    return np.asarray(query / norm, dtype=compute_dtype)
