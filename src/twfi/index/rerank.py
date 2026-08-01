"""Cross-encoder reranking: the F3 rung, and the second half of protocol 2.5's pipeline.

Protocol 2.5 fixes ``top_k_retrieve=20`` and ``top_k_rerank=5``, which is a two-stage pipeline:
retrieval hands twenty candidates to a cross-encoder, the cross-encoder returns five, and
protocol 3.2's Recall@5 is judged on those five. Until now only the first stage existed, so
"Recall@5" meant the retrieval top five and F3 had nothing to switch on.

**Why a cross-encoder is a different thing from the retrievers, not a better one.** BM25 and the
dense index score a query against a document *independently* -- the document's representation is
computed once, at build time, without knowing the query. A cross-encoder reads the pair together,
so it can use the interaction that a dot product has already thrown away: which clause the number
belongs to, whether 「台塑」 in the query is the subject of the sentence containing the figure or
merely nearby. That is why it is worth a rung of its own and why it can only be applied to a
shortlist -- scoring 4,796 chunks per query pairwise is not a retrieval system.

**One code path, one switch.** :class:`Reranker` does not reach into retrieval; it takes hits and
returns hits. F2 is ``search(q, 20)`` truncated to five, F3 is ``search(q, 20)`` reranked to five.
The retrieval half is byte-identical between the two rungs, which is the only way the difference
between their scores is attributable to reranking rather than to two implementations that happen
to differ.

Scores are *replaced*, not blended. A cross-encoder logit and an RRF score are on unrelated
scales, and any weighted sum of them makes the mixing weight a free parameter nobody measured --
the same argument :mod:`twfi.index.fusion` makes for not blending BM25 with cosine.

The torch import lives inside :func:`load_cross_encoder`. Everything else here is pure and is
tested without a model, offline, in the default CPU-only environment.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from twfi.index.retrieve import Hit

__all__ = ["Reranker", "ScorePairs", "load_cross_encoder", "rerank_hits"]

#: Scores a query against each passage, in the order given. Injected rather than imported so the
#: whole module is testable without a model -- the same reason :class:`Retriever` injects its
#: embedder.
ScorePairs = Callable[[str, Sequence[str]], Sequence[float]]

#: What a cross-encoder reads of each chunk. Chunks reach ~1,200 characters and the embedder uses
#: 1024 tokens, so this matches it: truncating the reranker's view more aggressively than the
#: retriever's would let F3 lose evidence that F2 could see, which would show up as reranking
#: making things worse for a reason that is not reranking.
DEFAULT_MAX_LENGTH = 1024


def rerank_hits(
    query: str, hits: Sequence[Hit], *, score_pairs: ScorePairs, top_k: int
) -> list[Hit]:
    """Reorder ``hits`` by cross-encoder score and keep the best ``top_k``.

    Ties break by the original position, so a reranker that scores two chunks identically leaves
    them in the order retrieval produced rather than in an order that depends on dictionary
    iteration. Protocol 3.2's metrics read positions off this list, so an unstable tie would make
    a run irreproducible for a reason unrelated to the model.

    Raises:
        ValueError: If ``top_k`` is not positive, or the scorer returned a different number of
            scores than there were hits -- a silent length mismatch would pair each chunk with
            another chunk's score, which reorders the list into something meaningless while
            looking perfectly healthy.
    """
    if top_k <= 0:
        raise ValueError(f"top_k must be positive, got {top_k}")
    if not hits:
        return []
    scores = list(score_pairs(query, [hit.text for hit in hits]))
    if len(scores) != len(hits):
        raise ValueError(
            f"the reranker returned {len(scores)} scores for {len(hits)} hits; refusing to pair "
            "them up, because a mismatch reorders the list into something meaningless"
        )
    ordered = sorted(
        zip(hits, scores, range(len(hits)), strict=True),
        key=lambda item: (-item[1], item[2]),
    )
    return [
        Hit(
            chunk_index=hit.chunk_index,
            score=float(score),
            chunk_id=hit.chunk_id,
            doc_id=hit.doc_id,
            pages=hit.pages,
            section_path=hit.section_path,
            text=hit.text,
        )
        for hit, score, _ in ordered[:top_k]
    ]


@dataclass(frozen=True, slots=True)
class Reranker:
    """A scoring function plus the cutoff it applies, so callers hold one object."""

    score_pairs: ScorePairs
    top_k: int

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError(f"top_k must be positive, got {self.top_k}")

    def rerank(self, query: str, hits: Sequence[Hit]) -> list[Hit]:
        return rerank_hits(query, hits, score_pairs=self.score_pairs, top_k=self.top_k)


def load_cross_encoder(
    model: str = "BAAI/bge-reranker-v2-m3",
    *,
    device: str = "cpu",
    max_length: int = DEFAULT_MAX_LENGTH,
    batch_size: int = 16,
) -> tuple[ScorePairs, str | None]:
    """Load the cross-encoder and return ``(scorer, model revision)``.

    The revision travels with the scorer for the same reason the embedder's does: a rerank score
    produced by an unrecorded revision cannot be re-derived, and G1 asks that every number can be.

    Raises:
        RuntimeError: If the optional ``models`` extra is not installed.
    """
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:  # pragma: no cover - depends on the optional extra
        raise RuntimeError(
            "reranking needs the optional models extra: uv sync --extra models"
        ) from exc

    tokenizer = AutoTokenizer.from_pretrained(model)
    dtype = torch.float16 if device.startswith("cuda") else torch.float32
    encoder = (
        AutoModelForSequenceClassification.from_pretrained(model, dtype=dtype).to(device).eval()
    )
    revision = getattr(getattr(encoder, "config", None), "_commit_hash", None)

    def score(query: str, passages: Sequence[str]) -> list[float]:
        out: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(passages), batch_size):
                batch = list(passages[start : start + batch_size])
                encoded = tokenizer(
                    [query] * len(batch),
                    batch,
                    padding=True,
                    truncation=True,
                    max_length=max_length,
                    return_tensors="pt",
                ).to(device)
                logits = encoder(**encoded).logits
                # bge-reranker emits a single relevance logit per pair. Kept as a raw logit
                # rather than squashed: only the ordering is used, and a sigmoid changes no
                # ordering while making the numbers look like probabilities they are not.
                out.extend(logits.view(-1).float().cpu().tolist())
        return out

    return score, revision
