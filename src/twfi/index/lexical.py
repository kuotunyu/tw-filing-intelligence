"""BM25 over Chinese filing text, where the tokeniser decides whether any of it works.

Protocol 2 makes BM25 the whole of F0's and F1's retrieval, and half of F2's RRF fusion. That
puts a lexical index under the study's baseline: if it under-retrieves for a reason unrelated
to parsing, every factor above it inherits the loss and the ladder's deltas stop being
attributable to the factor they are named after.

The failure this module exists to prevent is specific. Chinese has no word delimiters, so the
obvious tokeniser -- ``str.split()``, or ``\\w+`` -- returns one token per sentence. Document
frequency is then 1 almost everywhere, every document's terms are unique to it, and a query
scores above zero only if it repeats a whole sentence verbatim. BM25 does not fail loudly in
that state: it returns a short list of near-arbitrary documents, which is indistinguishable
from a corpus that simply does not contain the answer.

So CJK runs are cut into character bigrams and ASCII/digit runs are kept whole:

* **Bigrams, not segmentation.** No jieba or equivalent, and not only because of the
  no-new-dependency rule: a segmenter is a second model inside the baseline, its mistakes
  would land in F0's numbers with nothing to attribute them to, and 台積電 / 台積 / 積電 is
  exactly the sort of boundary that a segmenter decides differently in a query than in a
  305-page filing. Bigrams are the standard CJK IR baseline, and :mod:`twfi.eval.leakage`
  already uses them for near-duplicate detection, so the repository stays consistent with
  itself.
* **Figures and codes survive intact.** ``530,738,356`` is one term rather than a bag of
  digit fragments, and so are ``2330``, ``CoWoS`` and ``FY2023``. Splitting a figure on its
  thousands separators would make it match every other figure that happens to share a group
  of three digits -- the worst available behaviour in a corpus that is mostly figures.
* **Query and document are canonicalised the same way.** :func:`tokenise` applies NFC
  (D-024: ``1301-FY2023-AR`` writes 年 as U+F98E, so 「年度」 does not appear on 91 pages that
  show it) and folds fullwidth alphanumerics. Both are applied *here* rather than in
  :mod:`twfi.parsing.normalise` because a query does not come through the parser, and a
  query written ＦＹ２０２３ must reach the same term as a filing that writes FY2023. The
  fold is deliberately narrower than NFKC, which normalise.py rejected for rewriting the
  CJK punctuation that other modules compare literally.

Two refusals, both of the same kind -- an index that cannot support a claim must not be
searched:

* **A corpus that tokenised to nothing is refused at build.** In this corpus that means a text
  layer which produced no characters -- ``2317-FY2024-AR`` measures 0% readable -- and an index
  built from it answers every query with an empty list, which reads downstream as "the corpus
  does not contain the answer".
* **A stale index is refused at load**, the same way :func:`twfi.index.embeddings.load_vectors`
  refuses one: a document count that disagrees with the corpus means the postings belong to a
  previous chunking, and the symptom would be bad retrieval -- which is the thing F1 and F2
  are trying to measure.

Pure Python and CPU only. Nothing here imports a model.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Final

from twfi.parsing.normalise import normalise

__all__ = [
    "LEXICAL_MANIFEST",
    "TOKENISER_ID",
    "Bm25Config",
    "Bm25Index",
    "Bm25Manifest",
    "load_index",
    "save_index",
    "tokenise",
]

#: Recorded in every manifest and checked on load. Two indexes built by different tokenisers
#: are different retrievers, so comparing them would confound the factor under test.
TOKENISER_ID: Final = "cjk-bigram-ascii-v1"

#: A run of ASCII alphanumerics, plus ``.`` and ``,`` when they sit *between digits*. The
#: lookbehind is what keeps ``530,738,356`` and ``66.25`` whole while leaving ``附註.本期``
#: to split: a separator only belongs to a figure if a digit precedes it.
_WORD = r"[0-9A-Za-z]+(?:(?<=[0-9])[.,][0-9]+)*"

#: Codepoints treated as CJK and therefore cut into bigrams. Escapes rather than literal
#: ideographs, because a class written 一-鿿 cannot be reviewed for what it covers.
_CJK = (
    "\u3007"  # 〇 alone: 二〇二三年 is a date, and it sits in neither ideograph block
    "\u3400-\u4dbf"  # extension A
    "\u4e00-\u9fff"  # unified ideographs: effectively all of a filing
    "\uf900-\ufaff"  # compatibility ideographs -- NFC removes most (D-024), not all
    "\U00020000-\U0002a6df"  # extension B
)

_TOKEN_PATTERN: Final = re.compile(rf"(?P<word>{_WORD})|(?P<cjk>[{_CJK}]+)")

#: Fullwidth digits and Latin letters to their ASCII forms. NFKC would do this and much more;
#: normalise.py explains why the "much more" is not acceptable in this repository.
_FULLWIDTH_FOLD: Final[dict[int, int]] = {
    **{code: code - 0xFEE0 for code in range(0xFF10, 0xFF1A)},
    **{code: code - 0xFEE0 for code in range(0xFF21, 0xFF3B)},
    **{code: code - 0xFEE0 for code in range(0xFF41, 0xFF5B)},
}


def tokenise(text: str) -> tuple[str, ...]:
    """Split filing text into BM25 terms: CJK bigrams, ASCII/digit runs kept whole.

    Positions are not preserved and the result is deliberately lossy -- punctuation and
    whitespace are dropped, because they occur in every document and discriminate nothing.

    A CJK run of one character is emitted as itself. It has no bigram, and dropping it would
    make 「元」 or a lone 年 unfindable rather than merely weak.
    """
    if not text:
        return ()
    prepared = normalise(text).translate(_FULLWIDTH_FOLD)
    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(prepared):
        word = match.group("word")
        if word is not None:
            tokens.append(word.lower())
            continue
        run = match.group("cjk")
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[position : position + 2] for position in range(len(run) - 1))
    return tuple(tokens)


@dataclass(frozen=True, slots=True)
class Bm25Config:
    """The two BM25 knobs, with Robertson's defaults.

    Protocol 2.5 fixes ``top_k_retrieve``, ``top_k_rerank`` and RRF's ``k``; it says nothing
    about ``k1`` and ``b``, so the standard values stand. Whatever they are, they must be the
    same for F0 through F7 -- retuning them per factor would move retrieval strength between
    rungs and make ``Δ(Fk)`` unattributable.
    """

    #: Term-frequency saturation. 0 means "presence only": a term counts once however often
    #: it occurs, which is legal here and occasionally what a caller wants.
    k1: float = 1.2
    #: Length normalisation. 0 ignores document length, 1 divides fully by it.
    b: float = 0.75

    def __post_init__(self) -> None:
        if self.k1 < 0:
            raise ValueError(f"k1 must be at least 0, got {self.k1}")
        if not 0.0 <= self.b <= 1.0:
            raise ValueError(f"b must be within 0..1, got {self.b}")


def _idf(document_frequency: int, total_documents: int) -> float:
    """Robertson-Sparck-Jones idf with Lucene's ``+1``, which keeps the weight positive.

    The unsmoothed form turns negative once a term appears in more than half the corpus, and a
    negative weight *penalises* a document for containing a common word: a query holding both
    公司 and a rare term could then rank a document lacking the rare term above one that has
    it. Positive-but-tiny is the honest weight for a term that discriminates nothing.
    """
    return math.log(1.0 + (total_documents - document_frequency + 0.5) / (document_frequency + 0.5))


def _length_norms(doc_lengths: Sequence[int], config: Bm25Config) -> tuple[float, ...]:
    """Per-document ``k1 * (1 - b + b * dl / avgdl)``, computed once at build or load time.

    Derived, never stored: a saved norm could disagree with the ``k1``/``b`` saved beside it,
    and then the ranking would not be the one the manifest describes.

    Raises:
        ValueError: If there are documents but no terms at all.
    """
    if not doc_lengths:
        return ()
    total = sum(doc_lengths)
    if total == 0:
        raise ValueError(
            f"all {len(doc_lengths)} documents tokenised to zero terms; such an index scores "
            "every query at nothing, which downstream is indistinguishable from a corpus that "
            "lacks the answer -- check the text layer before indexing it"
        )
    average = total / len(doc_lengths)
    return tuple(
        config.k1 * (1.0 - config.b + config.b * length / average) for length in doc_lengths
    )


@dataclass(frozen=True, slots=True)
class Bm25Index:
    """An inverted index and the lengths needed to normalise against it.

    Documents are identified by their position in the sequence passed to :meth:`build`, which
    is the caller's chunk order. The index deliberately stores no chunk text and no chunk ids:
    what it can be checked against is a *count*, and the count is what :func:`load_index`
    refuses on.
    """

    config: Bm25Config
    #: term -> {document index: term frequency}
    postings: Mapping[str, Mapping[int, int]]
    doc_lengths: tuple[int, ...]
    length_norms: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.length_norms) != len(self.doc_lengths):
            raise ValueError(
                f"{len(self.length_norms)} length norms for {len(self.doc_lengths)} documents"
            )

    @classmethod
    def build(cls, documents: Sequence[str], config: Bm25Config | None = None) -> Bm25Index:
        """Index a corpus in the caller's order.

        An empty corpus is allowed and yields an empty index; a non-empty corpus that produces
        no terms is refused (see :func:`_length_norms`).
        """
        settings = config or Bm25Config()
        postings: dict[str, dict[int, int]] = {}
        doc_lengths: list[int] = []
        for doc_index, document in enumerate(documents):
            terms = tokenise(document)
            doc_lengths.append(len(terms))
            for term, count in Counter(terms).items():
                postings.setdefault(term, {})[doc_index] = count
        return cls(
            config=settings,
            postings=postings,
            doc_lengths=tuple(doc_lengths),
            length_norms=_length_norms(doc_lengths, settings),
        )

    def __len__(self) -> int:
        return len(self.doc_lengths)

    @property
    def vocabulary_size(self) -> int:
        """Number of distinct terms. Diagnostic only: nothing is gated on it."""
        return len(self.postings)

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Rank documents against a query, best first.

        Returns ``(document index, score)`` in descending score, ties broken by ascending
        document index so that two runs over the same corpus produce the same evidence set --
        F0's fixed top-k would otherwise vary between runs for no measurable reason.

        Only documents sharing at least one term appear, so the list is often shorter than
        ``top_k``. Padding it with zero-scoring documents would hand the generator context
        that matches nothing and that it may still cite.

        Raises:
            ValueError: If ``top_k`` is not positive. Returning ``[]`` instead would present a
                configuration mistake as a retrieval miss, and the two need different fixes.
        """
        if top_k <= 0:
            raise ValueError(f"top_k must be positive, got {top_k}")
        query_terms = Counter(tokenise(query))
        if not query_terms or not self.doc_lengths:
            return []
        total_documents = len(self.doc_lengths)
        scores: dict[int, float] = {}
        for term, query_count in query_terms.items():
            entries = self.postings.get(term)
            if not entries:
                continue
            idf = _idf(len(entries), total_documents)
            for doc_index, count in entries.items():
                saturated = count * (self.config.k1 + 1.0) / (count + self.length_norms[doc_index])
                # Weighting by the query's own term frequency is the k3 -> infinity limit of
                # the full formula. It matters for bigrams: 「營業收入及營業成本」 mentions
                # 營業 twice, and that is evidence about what was asked, not noise.
                scores[doc_index] = scores.get(doc_index, 0.0) + query_count * idf * saturated
        ranked = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
        return ranked[:top_k]


@dataclass(frozen=True, slots=True)
class Bm25Manifest:
    """What an index on disk is, in enough detail to reject it.

    Mirrors :class:`twfi.index.embeddings.EmbeddingManifest`, with ``tokeniser`` in place of
    the model revision: for BM25 the tokeniser *is* the model.
    """

    parser: str
    rows: int
    config: Bm25Config
    built_at: str
    tokeniser: str = TOKENISER_ID
    documents: tuple[str, ...] = ()
    notes: str = ""
    chunk_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "parser": self.parser,
            "rows": self.rows,
            "tokeniser": self.tokeniser,
            "config": asdict(self.config),
            "built_at": self.built_at,
            "documents": list(self.documents),
            "notes": self.notes,
        }
        # As in the embedding manifest: the full chunk id list is the expensive part of this
        # file and the row count is the actual check; a head is enough to tell builds apart.
        if self.chunk_ids:
            payload["chunk_ids_head"] = list(self.chunk_ids[:5])
        return payload


#: The lexical half's manifest, named apart from the dense half's -- see
#: :data:`twfi.index.embeddings.VECTOR_MANIFEST` for what sharing one filename cost.
LEXICAL_MANIFEST = "postings.manifest.json"


def save_index(directory: Path, index: Bm25Index, manifest: Bm25Manifest) -> tuple[Path, Path]:
    """Write ``postings.json`` and its manifest together, never one without the other.

    Raises:
        ValueError: If the manifest does not describe this index. A manifest that names a
            different document count, or different ``k1``/``b`` than the ranking was produced
            with, documents an index that does not exist.
    """
    if manifest.rows != len(index):
        raise ValueError(f"manifest says {manifest.rows} rows, index holds {len(index)}")
    if manifest.config != index.config:
        raise ValueError(
            f"manifest declares {manifest.config} but the index scores with {index.config}"
        )
    if manifest.tokeniser != TOKENISER_ID:
        raise ValueError(
            f"manifest claims tokeniser {manifest.tokeniser!r}, this module produces "
            f"{TOKENISER_ID!r}"
        )
    directory.mkdir(parents=True, exist_ok=True)
    postings_path = directory / "postings.json"
    manifest_path = directory / LEXICAL_MANIFEST
    payload = {
        "doc_lengths": list(index.doc_lengths),
        # Postings as pair lists rather than an object keyed by document number: JSON keys are
        # strings, and a file that says "12" where it means 12 invites a loader to get it wrong.
        "postings": {
            term: [[doc_index, count] for doc_index, count in sorted(entries.items())]
            for term, entries in index.postings.items()
        },
    }
    # sort_keys makes two builds of the same corpus byte-identical, so the index can be hashed
    # for provenance. ensure_ascii=False keeps CJK terms greppable when diagnosing a miss.
    postings_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path.write_text(
        json.dumps(manifest.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return postings_path, manifest_path


def load_index(
    directory: Path, *, expect_documents: int | None = None
) -> tuple[Bm25Index, dict[str, Any]]:
    """Load an index, refusing one that does not match its manifest or the current corpus.

    Raises:
        FileNotFoundError: If either file is absent -- half an index is not an index.
        ValueError: If the postings disagree with the manifest, if the manifest was written by
            a different tokeniser, or if the document count disagrees with ``expect_documents``.
    """
    postings_path = directory / "postings.json"
    manifest_path = directory / LEXICAL_MANIFEST
    for path in (postings_path, manifest_path):
        if not path.is_file():
            raise FileNotFoundError(f"{path} is missing; rebuild the index")
    manifest = _read_json_object(manifest_path)
    payload = _read_json_object(postings_path)

    if manifest.get("tokeniser") != TOKENISER_ID:
        raise ValueError(
            f"{directory} was built by tokeniser {manifest.get('tokeniser')!r}, this module is "
            f"{TOKENISER_ID!r}; the two rank differently, so rebuild rather than compare across"
        )
    doc_lengths = _read_doc_lengths(payload, postings_path)
    if len(doc_lengths) != manifest.get("rows"):
        raise ValueError(
            f"{directory} holds {len(doc_lengths)} documents but its manifest says "
            f"{manifest.get('rows')}; this index belongs to a different chunking"
        )
    if expect_documents is not None and len(doc_lengths) != expect_documents:
        raise ValueError(
            f"{directory} holds {len(doc_lengths)} documents but the corpus now has "
            f"{expect_documents} chunks; rebuild rather than searching a stale index"
        )
    config = _read_config(manifest, manifest_path)
    postings = _read_postings(payload, postings_path, document_count=len(doc_lengths))
    index = Bm25Index(
        config=config,
        postings=postings,
        doc_lengths=doc_lengths,
        length_norms=_length_norms(doc_lengths, config),
    )
    return index, manifest


def _read_json_object(path: Path) -> dict[str, Any]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} should hold a JSON object, found {type(payload).__name__}")
    return payload


def _read_config(manifest: Mapping[str, Any], path: Path) -> Bm25Config:
    """Score with the parameters the index was built with, not with this module's defaults."""
    block = manifest.get("config")
    if not isinstance(block, Mapping) or "k1" not in block or "b" not in block:
        raise ValueError(f"{path} records no k1/b; the ranking it describes cannot be reproduced")
    return Bm25Config(k1=float(block["k1"]), b=float(block["b"]))


def _read_doc_lengths(payload: Mapping[str, Any], path: Path) -> tuple[int, ...]:
    raw = payload.get("doc_lengths")
    if not isinstance(raw, list):
        raise ValueError(f"{path} has no doc_lengths list; rebuild the index")
    return tuple(int(value) for value in raw)


def _read_postings(
    payload: Mapping[str, Any], path: Path, *, document_count: int
) -> dict[str, Mapping[int, int]]:
    """Parse the inverted index, refusing a posting that points outside the corpus.

    :meth:`Bm25Index.build` cannot produce one; a file on disk can, and the symptom would be an
    ``IndexError`` from inside :meth:`Bm25Index.search` long after the cause.
    """
    raw = payload.get("postings")
    if not isinstance(raw, dict):
        raise ValueError(f"{path} has no postings object; rebuild the index")
    postings: dict[str, Mapping[int, int]] = {}
    for term, entries in raw.items():
        row: dict[int, int] = {}
        for entry in entries:
            doc_index = int(entry[0])
            if not 0 <= doc_index < document_count:
                raise ValueError(
                    f"{path}: term {term!r} has a posting for document {doc_index}, but the "
                    f"index holds {document_count} documents; rebuild it"
                )
            row[doc_index] = int(entry[1])
        postings[str(term)] = row
    return postings
