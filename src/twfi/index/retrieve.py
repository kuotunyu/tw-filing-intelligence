"""Put the three halves together: BM25, dense, and their rank fusion, over one chunk set.

This is the piece the factor ladder actually calls. F0 and F1 retrieve lexically, F2 fuses in
dense retrieval, and the difference between those two numbers is the whole of what F2 claims --
so the two modes have to be the *same* code path with one input switched, not two
implementations that might differ for reasons nobody measured.

Runs on CPU end to end. The corpus vectors were computed once on the GPU and persisted; a
query needs one embedding, which bge-m3 does on CPU in about 55 ms (measured). Nothing here
needs the card, which matters because it means retrieval can be developed and evaluated while
another project is training.

What this deliberately does not do is score-level blending. The fusion module takes ranks and
there is no argument through which a raw score can reach it: BM25 scores are unbounded while
cosine similarities live in [-1, 1], so any weighted sum is dominated by whichever happens to
have the larger scale, and the mixing weight becomes a free parameter nobody measured.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from twfi.index.dense import search as dense_search
from twfi.index.fusion import RrfConfig, reciprocal_rank_fusion
from twfi.index.lexical import Bm25Index

__all__ = [
    "Hit",
    "Mode",
    "Retriever",
    "characters_of",
    "covered_targets",
    "hit_rank",
    "recall_at_budget",
    "recall_at_k",
    "reciprocal_rank",
]

#: ``lexical`` is F0/F1, ``hybrid`` is F2 onward. Named after what they are rather than after
#: the factor, so a change to the ladder does not require renaming a retrieval mode.
Mode = Literal["lexical", "dense", "hybrid"]


@dataclass(frozen=True, slots=True)
class Hit:
    """One retrieved chunk, with the provenance G4 scores citations against."""

    chunk_index: int
    score: float
    chunk_id: str
    doc_id: str
    pages: tuple[int, ...]
    section_path: tuple[str, ...] = ()
    text: str = ""

    def cites(self, doc_id: str, page: int) -> bool:
        return self.doc_id == doc_id and page in self.pages


@dataclass(frozen=True, slots=True)
class Retriever:
    """A chunk set plus the two indexes over it.

    ``embed_query`` is injected rather than imported so the whole class is testable without a
    model: the tests pass a function returning fixed vectors, and the production caller passes
    one that runs bge-m3 on CPU. It also keeps torch out of this module's import graph.
    """

    chunks: Sequence[Mapping[str, Any]]
    bm25: Bm25Index | None = None
    vectors: np.ndarray | None = None
    embed_query: Callable[[str], np.ndarray] | None = None
    rrf: RrfConfig = field(default_factory=RrfConfig)
    #: How many candidates each side fetches before fusion, as an absolute count.
    #:
    #: ``None`` means "whatever ``top_k`` is", which is the degenerate setting: the fused list
    #: is then cut back to the length both sides already supplied, so a document only one side
    #: found can never reach the answer and fusion has nothing to recover.
    #:
    #: **The two parsers want opposite depths, and 100 is one of them.** Hybrid recall@10 on dev
    #: across depth None/20/50/100/200: baseline 7, 9, 11, **13**, 12; candidate **13**, 12, 9,
    #: 10, 10 (re-measured 2026-08-01). So 100 is baseline's peak and near candidate's floor,
    #: and the reported "candidate hybrid loses to candidate lexical" holds *at this depth*
    #: rather than being a property of the parser -- at ``None`` candidate hybrid is 13/15 and
    #: beats its own lexical 12/15. Both are dev-chosen, which protocol 1.3 permits, but the
    #: claim has to travel with the depth. None of these differences is significant (D-035).
    #:
    #: The earlier note here recorded the candidate move as 10/15 to 8/15 from D-029; on the
    #: rebuilt index the same comparison is 13/15 to 10/15. The direction survived, the literals
    #: did not.
    #:
    #: Absolute rather than a multiple of ``top_k``, and that is a correction. The first version
    #: scaled with ``top_k``, so recall at two cutoffs came from two different candidate pools --
    #: which is how a measured recall@20 landed *below* the same retriever's recall@10, an
    #: impossibility for one retriever at two cutoffs of one pool. Tuning a knob that makes k
    #: values incomparable would have selected something meaningless.
    #:
    #: Tunable on dev only (protocol 1.3).
    fetch_depth: int | None = None

    def __post_init__(self) -> None:
        if self.vectors is not None and len(self.vectors) != len(self.chunks):
            raise ValueError(
                f"{len(self.vectors)} vectors for {len(self.chunks)} chunks; this index belongs "
                "to a different chunking and searching it would return the wrong provenance"
            )
        if self.bm25 is not None and len(self.bm25) != len(self.chunks):
            raise ValueError(
                f"BM25 index holds {len(self.bm25)} documents for {len(self.chunks)} chunks"
            )

    def _hit(self, index: int, score: float) -> Hit:
        row = self.chunks[index]
        return Hit(
            chunk_index=index,
            score=score,
            chunk_id=str(row.get("chunk_id", "")),
            doc_id=str(row.get("doc_id", "")),
            pages=tuple(int(page) for page in row.get("pages", ()) or ()),
            section_path=tuple(str(part) for part in row.get("section_path", ()) or ()),
            text=str(row.get("text", "")),
        )

    def search(
        self,
        query: str,
        top_k: int,
        *,
        mode: Mode = "hybrid",
        allowed_doc_ids: Collection[str] | None = None,
    ) -> list[Hit]:
        """Retrieve, by whichever mode the factor under test uses.

        When ``allowed_doc_ids`` is supplied, both component rankings are filtered before
        truncation and before fusion.  Filtering a completed top-k would let another company's
        high-scoring chunks consume the candidate budget and leave an empty in-scope result.

        Raises:
            ValueError: If ``top_k`` is not positive, or the mode needs an index this
                retriever was not given. A retriever missing its dense index must not quietly
                answer as if it were lexical -- that would report F2's number while running
                F1, and the two are the entire comparison.
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        if mode in {"lexical", "hybrid"} and self.bm25 is None:
            raise ValueError(f"mode {mode!r} needs a BM25 index and this retriever has none")
        if mode in {"dense", "hybrid"} and (self.vectors is None or self.embed_query is None):
            raise ValueError(
                f"mode {mode!r} needs both the vectors and an embed_query function; refusing to "
                "fall back to lexical, which would report a hybrid number for a lexical run"
            )

        scope = frozenset(allowed_doc_ids) if allowed_doc_ids is not None else None
        deep = len(self.chunks) if scope is not None else top_k

        def in_scope(index: int) -> bool:
            return scope is None or str(self.chunks[index].get("doc_id", "")) in scope

        if mode == "lexical":
            assert self.bm25 is not None
            ranking = [
                (index, score) for index, score in self.bm25.search(query, deep) if in_scope(index)
            ]
            return [self._hit(index, score) for index, score in ranking[:top_k]]
        if mode == "dense":
            ranking = [
                (index, score) for index, score in self._dense(query, deep) if in_scope(index)
            ]
            return [self._hit(index, score) for index, score in ranking[:top_k]]

        assert self.bm25 is not None
        # Each side fetches at least top_k, and usually more, so a document only one side found
        # can still reach the fused answer. The pool does not depend on top_k, which is what
        # makes recall at two cutoffs comparable.
        deep = len(self.chunks) if scope is not None else max(top_k, self.fetch_depth or top_k)
        lexical_ranking = [index for index, _ in self.bm25.search(query, deep) if in_scope(index)]
        dense_ranking = [index for index, _ in self._dense(query, deep) if in_scope(index)]
        fused = reciprocal_rank_fusion([lexical_ranking, dense_ranking], self.rrf, top_k=top_k)
        return [self._hit(index, score) for index, score in fused]

    def _dense(self, query: str, top_k: int) -> list[tuple[int, float]]:
        assert self.vectors is not None and self.embed_query is not None
        vector = self.embed_query(query)
        return dense_search(self.vectors, np.asarray(vector, dtype=np.float32), top_k)


def recall_at_k(hits: Sequence[Hit], *, doc_id: str, pages: Sequence[int]) -> bool:
    """Whether any hit cites a page the gold record cites.

    Page-level rather than chunk-level on purpose: gold cites pages, a chunk may span two, and
    demanding the exact chunk would measure the chunker's boundaries rather than retrieval.

    An empty ``pages`` returns ``False`` rather than ``True``: a record naming no page gives
    retrieval nothing to hit, and counting that as success would inflate recall with records
    that never tested it.
    """
    if not pages:
        return False
    wanted = set(pages)
    return any(hit.doc_id == doc_id and wanted & set(hit.pages) for hit in hits)


def hit_rank(hits: Sequence[Hit], targets: Collection[tuple[str, int]]) -> int | None:
    """The 1-based rank of the first hit covering any required target, or ``None``.

    Targets are ``(document, page)`` pairs from a gold record's ``required_evidence``. Taking
    them as an explicit set rather than a single ``doc_id`` plus pages is what makes
    ``cross_document`` questions scoreable: their evidence lives in two filings, and a metric
    keyed on one document silently ignores half of it.
    """
    wanted = set(targets)
    if not wanted:
        return None
    for position, hit in enumerate(hits, start=1):
        if any((hit.doc_id, page) in wanted for page in hit.pages):
            return position
    return None


def reciprocal_rank(hits: Sequence[Hit], targets: Collection[tuple[str, int]], *, k: int) -> float:
    """Protocol 3.2's MRR contribution for one question: ``1/rank``, or 0 beyond ``k``.

    Zero rather than omitted when nothing is found within ``k``: dropping unfound questions from
    the mean would make a retriever look better the more often it failed completely.
    """
    rank = hit_rank(hits, targets)
    return 1.0 / rank if rank is not None and rank <= k else 0.0


def covered_targets(
    hits: Sequence[Hit], targets: Collection[tuple[str, int]], *, k: int
) -> frozenset[tuple[str, int]]:
    """Which required targets the top ``k`` hits collectively reach.

    The input to protocol 3.2's complete-evidence and cross-page coverage metrics. Collectively,
    because a question whose evidence spans two pages is answerable only if *both* arrive -- and
    Recall@5, which asks whether *any* of them arrived, cannot see the difference.
    """
    wanted = set(targets)
    found: set[tuple[str, int]] = set()
    for hit in hits[:k]:
        found.update((hit.doc_id, page) for page in hit.pages if (hit.doc_id, page) in wanted)
    return frozenset(found)


def characters_of(text: str) -> int:
    """A chunk's size for budgeting: characters with whitespace removed.

    Whitespace removed because the two parsers lay it out differently -- the layout parser keeps
    one cell per line where the baseline runs a page together -- and a budget that counted it
    would spend itself on formatting rather than on content. Measuring the corpus both ways gave
    2,161,264 against 2,161,561 characters, so the two parsers recover the same text and differ
    only in whitespace; a comparison that let whitespace count would be comparing layout.
    """
    return len("".join(text.split()))


def recall_at_budget(
    hits: Sequence[Hit], *, doc_id: str, pages: Sequence[int], budget: int
) -> bool:
    """Whether the gold page is found before a fixed *character* budget is spent.

    The fair cross-parser comparison, and the reason `recall_at_k` is not one (D-030). At one
    ``top_k`` the baseline returns roughly eight times the text -- its chunks have a median of
    800 characters against the candidate's 317 -- so recall at a fixed chunk count rewards
    whichever parser packs more into a chunk. That is a chunk-size difference wearing a
    retrieval result's clothes.

    Hits are consumed in rank order and each one's characters are charged in full, *including*
    text a previous hit already delivered. The baseline's fixed windows overlap by 100 of every
    800 characters, so it pays for that text twice -- which is correct, not a penalty: a
    retrieval budget spent re-delivering what the reader already has is spent. Deduplicating
    would credit the baseline for redundancy the answering model still has to read.

    The chunk that crosses the budget is *included*. Excluding it would make the metric depend
    on chunk size again in the opposite direction, since a parser with large chunks would have
    its last and possibly decisive hit discarded more often.
    """
    if not pages or budget <= 0:
        return False
    wanted = set(pages)
    spent = 0
    for hit in hits:
        if hit.doc_id == doc_id and wanted & set(hit.pages):
            return True
        spent += characters_of(hit.text)
        if spent >= budget:
            return False
    return False
