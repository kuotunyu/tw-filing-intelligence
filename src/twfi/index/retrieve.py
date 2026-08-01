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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from twfi.index.dense import search as dense_search
from twfi.index.fusion import RrfConfig, reciprocal_rank_fusion
from twfi.index.lexical import Bm25Index

__all__ = ["Hit", "Mode", "Retriever", "recall_at_k"]

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

    def search(self, query: str, top_k: int, *, mode: Mode = "hybrid") -> list[Hit]:
        """Retrieve, by whichever mode the factor under test uses.

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

        if mode == "lexical":
            assert self.bm25 is not None
            return [self._hit(index, score) for index, score in self.bm25.search(query, top_k)]
        if mode == "dense":
            return [self._hit(index, score) for index, score in self._dense(query, top_k)]

        assert self.bm25 is not None
        # Each side retrieves top_k so fusion has something to disagree about; taking fewer
        # would make the fused list mostly whichever side happened to rank first.
        lexical_ranking = [index for index, _ in self.bm25.search(query, top_k)]
        dense_ranking = [index for index, _ in self._dense(query, top_k)]
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
