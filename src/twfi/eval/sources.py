"""Which question types each filing can actually source.

A single ``usable`` flag per document turned out to be too coarse to write questions
against. ``2330-FY2024-FS`` is 91% readable and contains the FY2024 revenue figure --
in the notes, because the four primary statements have no text layer. Calling it
"usable" invites a ``table_cell`` question aimed at a statement page that extracts
nothing; calling it "unusable" discards a document that can host several question
types. Neither label is true.

So capability is recorded per question type, derived from measurements rather than from
a reading of the verdict:

* narrative needs legible prose pages.
* ``table_cell`` needs tables the extractor actually finds -- wherever they are. Notes
  tables count; a statement page that yields nothing does not.
* ``chart_value_trend`` needs chart candidates carrying numeric labels. A schematic
  with no numbers cannot ground a value question (D-014).
* the numeric types need tabular figures with a stated unit.
* ``cross_page`` needs enough legible pages to span.

The derivation stays separate from the measuring so it can be tested against inputs
that never occur in the ten real documents, including ones that should be refused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "MIN_LEGIBLE_PAGES",
    "MIN_TABLES",
    "MIN_TABLES_WITH_UNIT",
    "MIN_LABELLED_CHARTS",
    "BLOCKING_VERDICTS",
    "StatementState",
    "DocumentCapability",
    "derive_capability",
    "coverage",
]

#: Below this a document cannot host a narrative or cross-page question: there is not
#: enough legible text to ask about, let alone to span two pages.
MIN_LEGIBLE_PAGES: Final = 10

#: One extracted table might be a layout artefact. Several mean the extractor works on
#: this filing.
MIN_TABLES: Final = 3

#: Separate and higher, because it turned out to be the scarce resource. Measured across
#: the ten filings, tables declaring a unit are 0-104 while tables are 55-513: most
#: extracted tables are layout, not statements. Reusing MIN_TABLES here qualified a
#: document for numeric questions on three unit-bearing tables, which is too thin to
#: source five questions from without repeating the same table.
MIN_TABLES_WITH_UNIT: Final = 5

#: Charts without numeric labels cannot ground a value question (D-014), so labelled
#: candidates are counted, not candidates.
MIN_LABELLED_CHARTS: Final = 2


#: Verdicts after which nothing this document yields can be trusted. Measured counts
#: stay meaningless rather than merely low: 2317-FY2024-AR extracts 66 "tables" and 47
#: "charts" from a text layer that produces zero legible pages, so those are mojibake
#: grids and unlabelled artwork, not evidence. Counting them as capability is how a
#: document the protocol declares unusable would end up sourcing questions.
BLOCKING_VERDICTS: Final[frozenset[str]] = frozenset(
    {"unusable_text_layer", "partially_unusable_text_layer", "too_short"}
)

#: How the statements relate to this filing, which a single boolean conflated.
StatementState = Literal["readable", "image_only", "absent_by_design"]


@dataclass(frozen=True, slots=True)
class DocumentCapability:
    """What one filing can be asked about, and why."""

    doc_id: str
    legible_pages: int
    tables: int
    tables_with_unit: int
    labelled_charts: int
    statements: StatementState
    #: Question types this document may source, as a sorted tuple.
    sources: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def statement_pages_readable(self) -> bool:
        return self.statements == "readable"

    def can_source(self, question_type: str) -> bool:
        return question_type in self.sources

    def to_json(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "legible_pages": self.legible_pages,
            "tables": self.tables,
            "tables_with_unit": self.tables_with_unit,
            "labelled_charts": self.labelled_charts,
            "statements": self.statements,
            "sources": list(self.sources),
            "notes": list(self.notes),
        }


def derive_capability(
    *,
    doc_id: str,
    verdict: str,
    legible_pages: int,
    tables: int,
    tables_with_unit: int,
    labelled_charts: int,
    statements: StatementState,
    image_only_runs: Sequence[tuple[int, int]] = (),
) -> DocumentCapability:
    """Decide which question types a filing can source, and record why.

    ``unanswerable`` is deliberately absent from every result. Whether a question is
    unanswerable is a property of the question, not of the document, and a filing that
    can source nothing else is exactly the wrong place to look for one -- the refusal
    would be correct for the wrong reason.
    """
    sources: list[str] = []
    notes: list[str] = []

    if verdict in BLOCKING_VERDICTS:
        # Checked before any count is consulted. An extractor run over a broken text
        # layer still returns tables and figures; they are artefacts, and treating them
        # as capability is exactly the mistake the first version of this made.
        return DocumentCapability(
            doc_id=doc_id,
            legible_pages=legible_pages,
            tables=tables,
            tables_with_unit=tables_with_unit,
            labelled_charts=labelled_charts,
            statements=statements,
            sources=(),
            notes=(
                f"verdict {verdict!r}: this filing sources no questions. Its "
                f"{tables} tables and {labelled_charts} labelled charts were extracted "
                "from an unreadable text layer and are artefacts, not evidence.",
            ),
        )

    has_prose = legible_pages >= MIN_LEGIBLE_PAGES
    if has_prose:
        sources.append("narrative_fact")
        sources.append("cross_page")
    else:
        notes.append(
            f"only {legible_pages} legible pages (<{MIN_LEGIBLE_PAGES}); no narrative "
            "or cross-page question can rest on this filing"
        )

    if tables >= MIN_TABLES:
        sources.append("table_cell")
        if tables_with_unit >= MIN_TABLES_WITH_UNIT:
            sources.append("numeric_calculation")
            sources.append("cross_period_comparison")
        else:
            notes.append(
                f"{tables} tables but only {tables_with_unit} declare a unit "
                f"(<{MIN_TABLES_WITH_UNIT}); a figure whose scale is unstated cannot be "
                "compared or computed with"
            )
    else:
        notes.append(f"only {tables} extractable tables (<{MIN_TABLES})")

    if labelled_charts >= MIN_LABELLED_CHARTS:
        sources.append("chart_value_trend")
    else:
        notes.append(
            f"only {labelled_charts} chart candidates carry numeric labels "
            f"(<{MIN_LABELLED_CHARTS}); a schematic cannot ground a value question"
        )

    if has_prose and tables >= MIN_TABLES:
        sources.append("cross_document")

    if statements == "image_only":
        longest = max((last - first + 1 for first, last in image_only_runs), default=0)
        notes.append(
            "the primary statements are present but have no text layer"
            + (f" ({longest} consecutive pages)" if longest else "")
            + "; any figure must come from a note, and a question aimed at a statement "
            "page would cite evidence that extracts nothing"
        )
    elif statements == "absent_by_design":
        notes.append(
            "this filing contains no financial statements at all -- from FY2024 the "
            "股東會年報 does not embed them (D-012). Not a defect: narrative and chart "
            "questions are unaffected, and the figures live in the paired 財務報告書"
        )

    return DocumentCapability(
        doc_id=doc_id,
        legible_pages=legible_pages,
        tables=tables,
        tables_with_unit=tables_with_unit,
        labelled_charts=labelled_charts,
        statements=statements,
        sources=tuple(sorted(sources)),
        notes=tuple(notes),
    )


def coverage(capabilities: Mapping[str, DocumentCapability]) -> dict[str, list[str]]:
    """Which documents can source each question type.

    A question type with an empty list cannot be annotated at all, which is a finding
    about the corpus rather than a bug -- and one the protocol must state before the
    locked set is written, not discover afterwards.
    """
    found: dict[str, list[str]] = {}
    for doc_id, capability in sorted(capabilities.items()):
        for question_type in capability.sources:
            found.setdefault(question_type, []).append(doc_id)
    return found
