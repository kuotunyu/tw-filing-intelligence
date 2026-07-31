"""Detect anything that would let locked material influence a tuning decision.

The study's only real defence against overfitting is that dev and locked are disjoint
at the *company* level, so no paragraph, table, or figure from a locked filing can have
been seen while thresholds, prompts, chunk sizes or top-k were being chosen.

That defence has more ways to fail than "the same company appears twice":

* A question can be copied, or lightly reworded, from dev into locked. The wording
  differs, so an id check passes, while the answer was effectively known in advance.
* The chart challenger (protocol 2.3) selects a *model*. Selecting it on locked crops
  is tuning on locked data even though no threshold was touched.
* A gold answer can be attributed to a machine by hand-editing the file, which the
  types cannot prevent because the types are not what is on disk.

Near-duplicate detection uses character bigrams rather than whitespace tokens: Chinese
questions have no spaces, so word-level overlap would score two rewordings of the same
question as entirely different.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from twfi.eval.gold import GoldRecord, GoldSet
from twfi.protocol import DEV_COMPANY_CODES, LOCKED_COMPANY_CODES

__all__ = [
    "NEAR_DUPLICATE_THRESHOLD",
    "bigrams",
    "similarity",
    "near_duplicates",
    "leakage_problems",
]

#: Jaccard overlap of character bigrams above which two questions are treated as the
#: same question. Chosen to catch rewordings and unit changes, not merely typos.
NEAR_DUPLICATE_THRESHOLD = 0.80

#: Which side of the split each file is allowed to draw on. The challenger sits on the
#: dev side because it decides a model before the locked run.
_EXPECTED_CODES: Mapping[GoldSet, frozenset[str]] = {
    "dev": DEV_COMPANY_CODES,
    "locked": LOCKED_COMPANY_CODES,
    "probe": LOCKED_COMPANY_CODES,
    "challenger": DEV_COMPANY_CODES,
}


def _normalise(question: str) -> str:
    return "".join(question.split()).casefold()


def bigrams(question: str) -> frozenset[str]:
    """Character bigrams of a question, with whitespace removed."""
    text = _normalise(question)
    if len(text) < 2:
        return frozenset({text}) if text else frozenset()
    return frozenset(text[i : i + 2] for i in range(len(text) - 1))


def similarity(left: str, right: str) -> float:
    """Jaccard overlap of two questions' character bigrams, in ``[0, 1]``."""
    first, second = bigrams(left), bigrams(right)
    if not first or not second:
        return 1.0 if first == second else 0.0
    return len(first & second) / len(first | second)


def near_duplicates(
    left: Sequence[GoldRecord],
    right: Sequence[GoldRecord],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """Pairs of question ids, one from each side, that ask the same thing."""
    found: list[tuple[str, str, float]] = []
    cached = [(record, bigrams(record.question)) for record in right]
    for first in left:
        first_grams = bigrams(first.question)
        for second, second_grams in cached:
            if not first_grams or not second_grams:
                continue
            score = len(first_grams & second_grams) / len(first_grams | second_grams)
            if score >= threshold:
                found.append((first.question_id, second.question_id, round(score, 3)))
    return found


def leakage_problems(
    sets: Mapping[GoldSet, Sequence[GoldRecord]],
    *,
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> list[str]:
    """Return every way the given sets could leak locked material into tuning."""
    problems: list[str] = []
    problems.extend(_company_side_problems(sets))
    problems.extend(_overlap_problems(sets))
    problems.extend(_duplicate_problems(sets, threshold=threshold))
    problems.extend(_annotator_problems(sets))
    return problems


def _company_side_problems(sets: Mapping[GoldSet, Sequence[GoldRecord]]) -> list[str]:
    problems: list[str] = []
    for name, records in sets.items():
        allowed = _EXPECTED_CODES[name]
        for record in records:
            if record.company.code not in allowed:
                problems.append(
                    f"{name}: {record.question_id} uses company {record.company.code}, "
                    f"which is not on the {name} side of the split"
                )
    return problems


def _overlap_problems(sets: Mapping[GoldSet, Sequence[GoldRecord]]) -> list[str]:
    """Dev-side and locked-side files must share no company and no document."""
    dev_side = _codes(sets, ("dev", "challenger"))
    locked_side = _codes(sets, ("locked", "probe"))
    problems: list[str] = []

    shared_companies = sorted(dev_side.companies & locked_side.companies)
    if shared_companies:
        problems.append(f"dev and locked share companies: {shared_companies}")

    shared_documents = sorted(dev_side.documents & locked_side.documents)
    if shared_documents:
        problems.append(f"dev and locked share documents: {shared_documents}")
    return problems


class _Side:
    __slots__ = ("companies", "documents")

    def __init__(self) -> None:
        self.companies: set[str] = set()
        self.documents: set[str] = set()


def _codes(sets: Mapping[GoldSet, Sequence[GoldRecord]], names: tuple[GoldSet, ...]) -> _Side:
    side = _Side()
    for name in names:
        for record in sets.get(name, ()):
            side.companies.add(record.company.code)
            side.documents.update(record.source_document)
    return side


def _duplicate_problems(
    sets: Mapping[GoldSet, Sequence[GoldRecord]], *, threshold: float
) -> list[str]:
    """A question asked on both sides means the locked answer was known while tuning."""
    dev_records = [*sets.get("dev", ()), *sets.get("challenger", ())]
    locked_records = [*sets.get("locked", ()), *sets.get("probe", ())]
    return [
        f"{dev_id} and {locked_id} ask the same question (bigram overlap {score})"
        for dev_id, locked_id, score in near_duplicates(
            dev_records, locked_records, threshold=threshold
        )
    ]


def _annotator_problems(sets: Mapping[GoldSet, Sequence[GoldRecord]]) -> list[str]:
    """Verify on the loaded records, not only in the type.

    ``GoldRecord.annotator`` is ``Literal["human"]``, so this can only fail if a record
    reached memory without going through :func:`twfi.eval.gold.parse_record`. Checking
    anyway costs nothing and makes the guarantee visible at the point the gate is judged.
    """
    return [
        f"{name}: {record.question_id} claims annotator={record.annotator!r}"
        for name, records in sets.items()
        for record in records
        if record.annotator != "human"
    ]
