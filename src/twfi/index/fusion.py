"""Reciprocal rank fusion: combining BM25 and dense hits without inventing a free parameter.

F2 (protocol 2) adds dense retrieval alongside the baseline's BM25, so the two hit lists have
to be combined, and the study then attributes the F2 - F1 delta to "hybrid retrieval". That
attribution only holds if the combination itself contributes no tuned quantity. A weighted sum
of the two *scores* cannot meet that bar:

* BM25 scores are unbounded and corpus-dependent -- tens for a good match here, a different
  range on a different corpus or after a different tokenisation.
* Cosine similarities live in [-1, 1] by construction.
* So ``a * bm25 + b * cosine`` is dominated by whichever list happens to have the larger
  scale, and the weight that fixes that is a free parameter nobody measured. Choosing it after
  seeing locked results is what pre-registration forbids; choosing it before still leaves an
  arbitrary constant inside the number the report calls the benefit of hybrid retrieval.
  Normalising each list first does not escape this -- min-max over 20 hits is itself a choice,
  and it makes the fused order depend on the worst hit retrieved.

Rank fusion uses only each document's *position*, so multiplying either retriever's scores by
any positive constant cannot move the fused order. The one constant is ``k``, and protocol 2.5
froze it at 60 before any run: it is a pre-registered value, not a fitted one.
:func:`reciprocal_rank_fusion` therefore takes ranks and refuses to take scores at all -- there
is no argument through which a score can reach it.

Two smaller decisions, both about not letting an upstream quirk change a metric:

* **Ties break by ascending document id.** Recall@5 and MRR@10 read positions off this list, so
  a tie resolved by dict ordering would make a reported metric depend on insertion order.
* **A duplicate id within one list counts once.** A retriever that emits the same chunk twice
  would otherwise vote twice, weighting its own list up -- reintroducing precisely the mixing
  weight that rank fusion exists to avoid.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = ["DEFAULT_K", "RrfConfig", "reciprocal_rank_fusion"]

#: Protocol 2.5, frozen before the locked run. Kept here as a named constant so a test can
#: pin it against the protocol document rather than against a literal buried in a signature.
DEFAULT_K: Final = 60


@dataclass(frozen=True, slots=True)
class RrfConfig:
    """The single constant in rank fusion.

    ``k`` damps the influence of the top ranks. Appearing in a list at all is worth
    ``1 / (k + rank)``; at ``k = 60`` the gap between rank 1 and rank 2 is 0.00026 while merely
    appearing is worth 0.0164, so two retrievers agreeing on a document outweighs one retriever
    ranking it first. That is the property hybrid retrieval is wanted for. A small ``k``
    inverts it: at ``k = 1`` rank 1 is worth 0.5 and rank 5 is worth 0.17, so a single list's
    top hit outvotes agreement further down.
    """

    k: int = DEFAULT_K

    def __post_init__(self) -> None:
        if self.k < 1:
            raise ValueError(
                f"RRF k must be at least 1, got {self.k}; k=0 removes the damping entirely "
                "(rank 1 scores 1.0) and a negative k divides by zero at rank -k and inverts "
                "the ordering before it"
            )


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    config: RrfConfig | None = None,
    top_k: int | None = None,
) -> list[tuple[int, float]]:
    """Fuse ranked id lists into one, scoring each id as the sum of ``1 / (k + rank)``.

    ``rank`` is 1-based within each list. A document present in several lists accumulates a
    term from each, which is what makes agreement between BM25 and dense retrieval outrank a
    single list's favourite; a document present in only one list still scores.

    Args:
        rankings: One ranked list of document ids per retriever, best first. Ids are whatever
            the caller uses to identify a chunk -- this function never dereferences them.
        config: Fusion constant. Defaults to the protocol's frozen ``k = 60``.
        top_k: Truncate the fused list. ``None`` returns every document that appeared
            anywhere; a value above that count returns everything rather than raising.

    Returns:
        ``(document id, fused score)`` descending by score, ties by ascending id.

    Raises:
        ValueError: If ``rankings`` is empty, or ``top_k`` is given and not positive.
    """
    settings = config or RrfConfig()
    if not rankings:
        raise ValueError(
            "no rankings were given, so there is nothing to fuse; pass at least one list. "
            "Returning an empty result here would be recorded as a retrieval miss instead of "
            "the wiring error it is, and the error analysis buckets would be wrong"
        )
    if top_k is not None and top_k <= 0:
        raise ValueError(f"top_k must be positive when given, got {top_k}; pass None for all")

    scores: dict[int, float] = {}
    for ranking in rankings:
        # Rank over *distinct* ids rather than over positions. Ranking by raw position would
        # let a duplicate at position 2 push everything behind it down one rank, so an upstream
        # de-duplication bug would change the scores of documents it never touched. Fusing
        # [7, 7, 3] and fusing [7, 3] give the same result here, which is the property wanted.
        rank = 0
        seen: set[int] = set()
        for document in ranking:
            if document in seen:
                continue
            seen.add(document)
            rank += 1
            scores[document] = scores.get(document, 0.0) + 1.0 / (settings.k + rank)

    # Accumulation follows the order of `rankings`, identically for every document, so the sums
    # are reproducible run to run; the explicit id tie-break then makes the order total.
    fused = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return fused if top_k is None else fused[:top_k]
