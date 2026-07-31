"""The gold set, as types that cannot hold an ungrounded answer.

Two properties of the gold set decide whether this study means anything, so both are
enforced by the type system rather than by a reviewer remembering to check.

**Who wrote the answer.** ``GoldRecord.annotator`` names it -- ``"human"`` or the model
that drafted it. It was ``Literal["human"]`` until annotating 24 questions by hand made
clear the remaining 48 would not get done, and a study that never runs measures nothing.

The rule that matters is narrower than "a person typed it". Gold must not come from the
*candidate* (qwen3.6:27b behind the retrieval pipeline) or from this repository's own
extractor, because either would be grading a system against itself. A different model
reading a rendered page image violates neither. And on transcription the human is not the
more reliable party: the one wrong answer in this set so far, PROBE-0004, was a human slip
that a machine check caught.

What a person is irreplaceable for is *choosing* the questions -- an annotator who also
writes the answers can drift toward questions the pipeline handles well. That is what the
audit sample defends, so ``audited`` records whether a person checked each specific record
against its page, and ``set_problems`` reports the rate. Nothing here can be hidden: every
record names its author, says whether it was audited, and the report prints both.

Drafts still live in :class:`DraftItem`, which has no ``answer`` field at all.

**Where the answer came from.** ``answer_provenance`` admits three origins: a human
reading the filing, a model reading a *rendered page image*, or an official TWSE
structured dataset. This repository's own table and figure extractors are deliberately
not representable. They are the thing under test: if gold table values came from the
extractor, a wrong extraction would become a wrong gold answer that the candidate --
running the same extractor -- would reproduce and be scored correct for. The measured
gain of factors F1 and F4 would be an artefact of grading a parser against itself.

Reading rendered pixels avoids that: it bypasses the text layer entirely, which is why
it is the only mode a model may use.

``docs/FEASIBILITY_PROTOCOL.md`` §1.5 is the authoritative schema for a human reader;
this module is the same schema in enforceable form.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Literal, get_args

from twfi.numeric.amounts import UNIT_SCALES, canonical_unit
from twfi.protocol import (
    ROUTE_BY_QUESTION_TYPE,
    USABLE_DOCUMENTS,
    QuestionType,
    split_for_company,
)

__all__ = [
    "GoldSet",
    "AnswerProvenance",
    "Annotator",
    "RefusalReasonClass",
    "EvidenceKind",
    "ALLOWED_UNITS",
    "NUMERIC_QUESTION_TYPES",
    "REQUIRED_EVIDENCE_KINDS",
    "ID_PREFIXES",
    "CompanyRef",
    "BBoxRef",
    "StructuredSourceKey",
    "EvidenceRef",
    "Tolerance",
    "default_tolerance",
    "GoldRecord",
    "DraftItem",
    "record_problems",
    "set_problems",
    "parse_record",
    "load_gold",
    "composition",
    "gold_route",
]

#: Which file a record belongs to. Probes and the chart challenger are graded by
#: different gates (G8, protocol 2.3) and so are kept apart from the answer sets.
GoldSet = Literal["dev", "locked", "probe", "challenger"]

#: Where a gold answer came from. This repository's own extractors are absent by design:
#: they are the thing under test (see the module docstring).
AnswerProvenance = Literal[
    "human_read_pdf",
    "model_read_rendered_page",
    "official_structured",
]

#: Who produced the answer. Named rather than asserted, so a reader can weigh it.
#: ``candidate`` is deliberately unrepresentable -- the system under test may never
#: supply its own gold.
Annotator = Literal["human", "claude-opus-5"]

#: Protocol 1.4. The three reasons a question can be unanswerable, all of which the
#: locked set must exercise.
RefusalReasonClass = Literal[
    "absent_from_documents",
    "outside_selected_scope",
    "irreconcilable_conflict",
]

EvidenceKind = Literal["page", "table_cell", "chart_crop", "sql_row"]

#: Monetary scales come from the numeric layer so a gold unit and a stored unit cannot
#: mean different things; the rest are the non-monetary units answers actually use.
ALLOWED_UNITS: Final[frozenset[str]] = frozenset(UNIT_SCALES) | frozenset(
    {"%", "倍", "股", "千股", "人", "年", "家"}
)

#: Types whose answer is a figure. Protocol 1.5: these need a machine-checkable
#: pointer -- a structured row or a bbox -- not merely a page number.
NUMERIC_QUESTION_TYPES: Final[frozenset[str]] = frozenset(
    {"table_cell", "numeric_calculation", "cross_period_comparison", "chart_value_trend"}
)

#: At least one of these evidence kinds must be present for the type. A chart question
#: whose only evidence is a page number cannot be used to score crop-level citation,
#: which is the whole point of measuring the chart route.
REQUIRED_EVIDENCE_KINDS: Final[MappingProxyType[str, frozenset[str]]] = MappingProxyType(
    {
        "narrative_fact": frozenset({"page"}),
        "table_cell": frozenset({"table_cell"}),
        "numeric_calculation": frozenset({"sql_row", "table_cell"}),
        "cross_period_comparison": frozenset({"sql_row", "table_cell"}),
        "chart_value_trend": frozenset({"chart_crop"}),
        "cross_page": frozenset({"page"}),
        "cross_document": frozenset({"page", "table_cell", "sql_row"}),
        "unanswerable": frozenset(),
    }
)

ID_PREFIXES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {"locked": "LOCK", "dev": "DEV", "probe": "PROBE", "challenger": "CHAL"}
)

_USABLE_DOC_IDS: Final[frozenset[str]] = frozenset(d.doc_id for d in USABLE_DOCUMENTS)
_DOC_COMPANY: Final[MappingProxyType[str, str]] = MappingProxyType(
    {d.doc_id: d.company_code for d in USABLE_DOCUMENTS}
)


# ------------------------------------------------------------------ value objects


@dataclass(frozen=True, slots=True)
class CompanyRef:
    name: str
    code: str


@dataclass(frozen=True, slots=True)
class BBoxRef:
    """A region on one page, in PDF points, as ``(x0, y0, x1, y1)``."""

    page: int
    bbox: tuple[float, float, float, float]

    def __post_init__(self) -> None:
        if self.page < 1:
            raise ValueError(f"page numbers are 1-based; got {self.page}")
        x0, y0, x1, y1 = self.bbox
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"bbox must have positive area; got {self.bbox}")


@dataclass(frozen=True, slots=True)
class StructuredSourceKey:
    """A pointer into the numeric store, precise enough to re-run."""

    table: str
    row_key: str

    def __post_init__(self) -> None:
        if not self.table.strip() or not self.row_key.strip():
            raise ValueError("a structured source key needs both a table and a row key")


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    kind: EvidenceKind
    ref: str

    def __post_init__(self) -> None:
        if not self.ref.strip():
            raise ValueError(f"{self.kind} evidence needs a reference")


@dataclass(frozen=True, slots=True)
class Tolerance:
    """How close a numeric answer must be to count as correct.

    ``relative`` is a fraction of the gold value; ``absolute`` is in the answer's own
    unit, which for a percentage means percentage points.
    """

    type: Literal["relative", "absolute"]
    value: float

    def __post_init__(self) -> None:
        if self.value <= 0:
            raise ValueError(f"tolerance must be positive; got {self.value}")

    def accepts(self, *, gold: float, candidate: float) -> bool:
        if self.type == "absolute":
            return abs(candidate - gold) <= self.value
        return abs(candidate - gold) <= abs(gold) * self.value


def default_tolerance(unit: str | None) -> Tolerance:
    """Protocol 3.4: 0.5% relative, except percentages at 0.1 percentage points.

    A relative tolerance on a percentage is the wrong instrument -- 0.5% of a 0.4%
    margin is 0.002pp, which no honest extraction would hit -- so ratios get an
    absolute band instead.
    """
    if canonical_unit(unit) == "%":
        return Tolerance("absolute", 0.1)
    return Tolerance("relative", 0.005)


# ----------------------------------------------------------------- the gold record


@dataclass(frozen=True, slots=True)
class GoldRecord:
    """One graded question. ``annotator`` has one admissible value, by design."""

    question_id: str
    question_type: QuestionType
    question: str
    answer: str | None
    company: CompanyRef
    period: str
    source_document: tuple[str, ...]
    required_evidence: tuple[EvidenceRef, ...]
    answer_provenance: AnswerProvenance
    annotated_at: dt.date
    answerable: bool = True
    acceptable_variants: tuple[str, ...] = ()
    unit: str | None = None
    currency: Literal["TWD", "USD"] | None = None
    statement_basis: Literal["consolidated", "parent_only"] | None = None
    source_url: tuple[str, ...] = ()
    page_numbers: tuple[int, ...] = ()
    bbox: tuple[BBoxRef, ...] = ()
    structured_source_key: StructuredSourceKey | None = None
    tolerance: Tolerance | None = None
    refusal_reason_class: RefusalReasonClass | None = None
    #: The figures this answer was computed from, exactly as printed on the page.
    #:
    #: A growth rate is not on the page; the two figures it comes from are. Recording
    #: them is what lets anyone re-run the arithmetic instead of trusting whoever did
    #: it -- the lesson from PROBE-0004, where a check was performed but not reported
    #: and so could not be confirmed to have been performed on the recorded number.
    #: Empty means the answer was read directly and must appear on the cited page.
    derived_from: tuple[str, ...] = ()
    annotation_notes: str = ""
    annotator: Annotator = "human"
    #: True when a person checked this specific record against its rendered page. The
    #: defence against a drafter drifting toward questions the pipeline handles well.
    audited: bool = False

    @property
    def is_trustworthy(self) -> bool:
        """Either a person wrote it, or a person checked it."""
        return self.annotator == "human" or self.audited

    @property
    def is_derived(self) -> bool:
        """Whether the answer was computed rather than read off the page."""
        return bool(self.derived_from)

    @property
    def route(self) -> str:
        return gold_route(self.question_type)

    @property
    def evidence_kinds(self) -> frozenset[str]:
        return frozenset(item.kind for item in self.required_evidence)


@dataclass(frozen=True, slots=True)
class DraftItem:
    """A proposed question slot with its mechanical fields pre-filled.

    Deliberately has no ``answer``, no ``annotator`` and no ``answer_provenance``. A
    draft is a pointer at evidence a person still has to read; it is not a gold record
    missing a field, and it cannot be promoted into one by adding a key.
    """

    draft_id: str
    question_type: QuestionType
    company: CompanyRef
    period: str
    source_document: tuple[str, ...]
    evidence_hint: tuple[EvidenceRef, ...]
    page_numbers: tuple[int, ...] = ()
    bbox: tuple[BBoxRef, ...] = ()
    structured_source_key: StructuredSourceKey | None = None
    unit: str | None = None
    currency: Literal["TWD", "USD"] | None = None
    source_url: tuple[str, ...] = ()
    #: Why this slot was surfaced -- what the tooling saw, never what the answer is.
    rationale: str = ""
    notes_for_annotator: str = ""


# --------------------------------------------------------------------- validation


def record_problems(record: GoldRecord, *, gold_set: GoldSet) -> list[str]:
    """Return every protocol violation in one record.

    Returns problems rather than raising so a validator can report all of them at
    once; annotating 72 questions and being told about one error per run would be a
    poor way to spend an afternoon.
    """
    problems: list[str] = []
    where = record.question_id

    expected_prefix = ID_PREFIXES[gold_set]
    if not record.question_id.startswith(f"{expected_prefix}-"):
        problems.append(f"{where}: id must start with {expected_prefix}- in the {gold_set} set")

    if not record.question.strip():
        problems.append(f"{where}: question text is empty")

    problems.extend(_answerability_problems(record))
    problems.extend(_evidence_problems(record))
    problems.extend(_source_problems(record, gold_set=gold_set))
    problems.extend(_unit_problems(record))
    return problems


def _answerability_problems(record: GoldRecord) -> list[str]:
    """``unanswerable``, ``answerable`` and ``answer`` must all say the same thing."""
    problems: list[str] = []
    where = record.question_id
    is_unanswerable = record.question_type == "unanswerable"

    if is_unanswerable != (not record.answerable):
        problems.append(f"{where}: question_type unanswerable and answerable=False must agree")

    if is_unanswerable:
        if record.answer is not None:
            problems.append(f"{where}: an unanswerable question must have answer=null")
        if record.refusal_reason_class is None:
            problems.append(f"{where}: an unanswerable question needs a refusal_reason_class")
        if record.tolerance is not None:
            problems.append(f"{where}: an unanswerable question has nothing to tolerance-check")
        return problems

    if record.answer is None or not record.answer.strip():
        problems.append(f"{where}: an answerable question needs a non-empty answer")
    if record.refusal_reason_class is not None:
        problems.append(f"{where}: refusal_reason_class belongs only on unanswerable questions")
    if record.question_type in NUMERIC_QUESTION_TYPES and record.tolerance is None:
        problems.append(f"{where}: {record.question_type} needs an explicit tolerance")
    if record.annotator != "human" and record.answer_provenance == "human_read_pdf":
        problems.append(
            f"{where}: annotator {record.annotator!r} cannot claim human_read_pdf; a "
            "model-drafted answer reads a rendered page, so say so"
        )
    if record.is_derived and len(record.derived_from) < 2:
        problems.append(
            f"{where}: a derived answer needs the figures it came from, so the arithmetic "
            "can be re-run by someone other than whoever did it"
        )
    return problems


def _evidence_problems(record: GoldRecord) -> list[str]:
    problems: list[str] = []
    where = record.question_id

    if record.question_type != "unanswerable" and not record.required_evidence:
        problems.append(f"{where}: required_evidence is the complete-evidence metric's input")

    wanted = REQUIRED_EVIDENCE_KINDS[record.question_type]
    if wanted and not (record.evidence_kinds & wanted):
        problems.append(
            f"{where}: {record.question_type} needs at least one of "
            f"{sorted(wanted)} in required_evidence, got {sorted(record.evidence_kinds)}"
        )

    if record.question_type in NUMERIC_QUESTION_TYPES and not (
        record.structured_source_key or record.bbox
    ):
        problems.append(f"{where}: a numeric answer needs a structured_source_key or a bbox")

    if "page" in record.evidence_kinds and not record.page_numbers:
        problems.append(f"{where}: page evidence requires page_numbers")

    off_page = sorted({ref.page for ref in record.bbox} - set(record.page_numbers))
    if record.page_numbers and off_page:
        problems.append(f"{where}: bbox pages {off_page} are not listed in page_numbers")

    if record.question_type == "cross_page" and len(set(record.page_numbers)) < 2:
        problems.append(f"{where}: a cross_page question must cite at least two pages")
    if record.question_type == "cross_document" and len(set(record.source_document)) < 2:
        problems.append(f"{where}: a cross_document question must cite at least two documents")
    if record.question_type == "cross_period_comparison" and "-" not in record.period:
        problems.append(
            f"{where}: a cross_period_comparison needs a span period like FY2023-FY2024, "
            f"got {record.period!r}"
        )
    return problems


def _source_problems(record: GoldRecord, *, gold_set: GoldSet) -> list[str]:
    """Sources must be declared, usable, and on the right side of the split."""
    problems: list[str] = []
    where = record.question_id

    if not record.source_document:
        problems.append(f"{where}: at least one source_document is required")

    for doc_id in record.source_document:
        if doc_id not in _USABLE_DOC_IDS:
            problems.append(f"{where}: {doc_id} is not a declared usable document")
            continue
        if _DOC_COMPANY[doc_id] != record.company.code:
            problems.append(
                f"{where}: {doc_id} belongs to {_DOC_COMPANY[doc_id]}, not {record.company.code}"
            )

    try:
        company_split = split_for_company(record.company.code)
    except KeyError:
        problems.append(f"{where}: {record.company.code} is not a study company")
        return problems

    if gold_set in {"dev", "locked"} and company_split != gold_set:
        problems.append(
            f"{where}: {record.company.code} is a {company_split} company "
            f"but appears in the {gold_set} set"
        )

    if record.answer_provenance == "official_structured" and record.structured_source_key is None:
        problems.append(
            f"{where}: an answer from an official dataset must name the row it came from"
        )
    return problems


def _unit_problems(record: GoldRecord) -> list[str]:
    problems: list[str] = []
    where = record.question_id

    if record.unit is not None:
        canonical = canonical_unit(record.unit)
        if canonical != record.unit:
            problems.append(f"{where}: unit {record.unit!r} should be written {canonical!r}")
        elif canonical not in ALLOWED_UNITS:
            problems.append(f"{where}: unit {record.unit!r} is not an allowed unit")

    monetary = record.unit is not None and canonical_unit(record.unit) in UNIT_SCALES
    if monetary and record.currency is None:
        problems.append(f"{where}: a monetary answer must state its currency")
    return problems


def set_problems(
    records: Sequence[GoldRecord],
    *,
    gold_set: GoldSet,
    type_counts: Mapping[str, int] | None = None,
) -> list[str]:
    """Return every violation across a whole set, including its composition.

    ``type_counts`` is checked only when given. Pass ``LOCKED_TYPE_COUNTS`` once the
    locked set claims to be finished; while it is being annotated, a partial set is
    progress rather than a pile of failures.
    """
    problems: list[str] = []

    seen: dict[str, int] = {}
    for record in records:
        problems.extend(record_problems(record, gold_set=gold_set))
        seen[record.question_id] = seen.get(record.question_id, 0) + 1
    problems.extend(
        f"duplicate question_id {qid}" for qid, count in sorted(seen.items()) if count > 1
    )

    questions: dict[str, str] = {}
    for record in records:
        key = " ".join(record.question.split()).casefold()
        if not key:
            # An unfilled template has several empty questions, and reporting them as
            # duplicates of each other buries the one problem that matters -- that they
            # are empty -- under noise the annotator then has to ignore.
            continue
        if key in questions:
            problems.append(f"{record.question_id} repeats the question text of {questions[key]}")
        else:
            questions[key] = record.question_id

    # Composition is a property of a *finished* set, so it is checked only when the
    # caller asks. Annotation is incremental: five of the locked set's thirty-six
    # questions is progress, and reporting it as thirty-one failures would bury the
    # record-level problems that actually need fixing.
    if type_counts is not None:
        actual = {qtype: 0 for qtype in get_args(QuestionType)}
        for record in records:
            actual[record.question_type] += 1
        for qtype, want in sorted(type_counts.items()):
            if actual.get(qtype, 0) != want:
                problems.append(
                    f"{gold_set} set needs {want} {qtype} questions, has {actual.get(qtype, 0)}"
                )

    drafted = [r for r in records if r.annotator != "human"]
    unchecked = [r for r in drafted if not r.audited]
    if drafted and type_counts is not None and len(unchecked) == len(drafted):
        problems.append(
            f"{gold_set}: all {len(drafted)} model-drafted record(s) are unaudited. A "
            "drafter that also chooses the questions can drift toward what the pipeline "
            "handles well, and nothing here would show it."
        )

    # Also completeness, and only when unanswerable questions are actually expected. A
    # caller passing an empty distribution is saying "no composition requirement", so
    # demanding all three causes of it would contradict what it asked for.
    if gold_set == "locked" and type_counts and type_counts.get("unanswerable", 0) > 0:
        classes = {r.refusal_reason_class for r in records if r.question_type == "unanswerable"}
        missing = sorted(set(get_args(RefusalReasonClass)) - classes)
        if missing:
            problems.append(
                f"locked unanswerable questions must cover every cause; missing {missing}"
            )
    return problems


# ------------------------------------------------------------------------- parsing


def parse_record(payload: Mapping[str, Any]) -> GoldRecord:
    """Build a record from one JSON object.

    Raises:
        ValueError: If a field is missing, malformed, or -- for ``annotator`` -- claims
            an origin the schema does not admit.
    """
    unknown = set(payload) - _FIELD_NAMES
    if unknown:
        raise ValueError(f"unknown gold fields: {sorted(unknown)}")

    annotator = payload.get("annotator", "human")
    if annotator not in get_args(Annotator):
        raise ValueError(
            f"annotator must be one of {sorted(get_args(Annotator))}, got {annotator!r}. "
            "The candidate system may never supply its own gold, and an unnamed author "
            "cannot be weighed by a reader."
        )

    if "answer_provenance" not in payload:
        raise ValueError(
            "gold record is missing required field 'answer_provenance'. Every answer "
            "must say whether a person read the filing or an official dataset supplied it."
        )
    provenance = payload["answer_provenance"]
    if provenance not in get_args(AnswerProvenance):
        raise ValueError(
            f"answer_provenance must be one of {sorted(get_args(AnswerProvenance))}, "
            f"got {provenance!r}"
        )

    try:
        return GoldRecord(
            question_id=str(payload["question_id"]),
            question_type=payload["question_type"],
            question=str(payload["question"]),
            answer=None if payload.get("answer") is None else str(payload["answer"]),
            company=CompanyRef(**payload["company"]),
            period=str(payload["period"]),
            source_document=tuple(payload["source_document"]),
            required_evidence=tuple(
                EvidenceRef(kind=item["kind"], ref=str(item["ref"]))
                for item in payload.get("required_evidence", ())
            ),
            answer_provenance=provenance,
            annotated_at=dt.date.fromisoformat(str(payload["annotated_at"])),
            answerable=bool(payload.get("answerable", True)),
            acceptable_variants=tuple(payload.get("acceptable_variants", ())),
            unit=payload.get("unit"),
            currency=payload.get("currency"),
            statement_basis=payload.get("statement_basis"),
            source_url=tuple(payload.get("source_url", ())),
            page_numbers=tuple(int(page) for page in payload.get("page_numbers", ())),
            bbox=tuple(
                BBoxRef(page=int(item["page"]), bbox=tuple(float(v) for v in item["bbox"]))  # type: ignore[arg-type]
                for item in payload.get("bbox", ())
            ),
            structured_source_key=(
                StructuredSourceKey(**payload["structured_source_key"])
                if payload.get("structured_source_key")
                else None
            ),
            tolerance=(
                Tolerance(
                    type=payload["tolerance"]["type"], value=float(payload["tolerance"]["value"])
                )
                if payload.get("tolerance")
                else None
            ),
            refusal_reason_class=payload.get("refusal_reason_class"),
            derived_from=tuple(payload.get("derived_from", ())),
            annotator=annotator,
            audited=bool(payload.get("audited", False)),
            annotation_notes=str(payload.get("annotation_notes", "")),
        )
    except KeyError as exc:
        raise ValueError(f"gold record is missing required field {exc.args[0]!r}") from exc


_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "question_id",
        "question_type",
        "question",
        "answer",
        "acceptable_variants",
        "unit",
        "currency",
        "period",
        "company",
        "statement_basis",
        "source_document",
        "source_url",
        "page_numbers",
        "bbox",
        "structured_source_key",
        "required_evidence",
        "answerable",
        "tolerance",
        "annotation_notes",
        "annotator",
        "annotated_at",
        "answer_provenance",
        "refusal_reason_class",
        "derived_from",
        "audited",
    }
)


def load_gold(lines: Iterable[str]) -> list[GoldRecord]:
    """Parse a JSONL gold file, naming the line number of whichever record is broken."""
    records: list[GoldRecord] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {number} is not valid JSON: {exc}") from exc
        try:
            records.append(parse_record(payload))
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
    return records


def composition(records: Sequence[GoldRecord]) -> dict[str, int]:
    """How a set was annotated, for the report to print verbatim.

    A study that leans on model-drafted gold has to say so and say how much, which means
    the numbers must be available without anyone choosing to compute them.
    """
    drafted = [r for r in records if r.annotator != "human"]
    return {
        "records": len(records),
        "human_annotated": sum(1 for r in records if r.annotator == "human"),
        "model_drafted": len(drafted),
        "model_drafted_audited": sum(1 for r in drafted if r.audited),
        "trustworthy": sum(1 for r in records if r.is_trustworthy),
    }


def gold_route(question_type: str) -> str:
    """The route a correct system must take, fixed by protocol 3.5."""
    return ROUTE_BY_QUESTION_TYPE[question_type]
