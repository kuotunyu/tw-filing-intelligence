"""Per-document capability, and the three ways the first version got it wrong.

Every measurement here is a real one, taken from `results/runs/question_sources.json`,
so the thresholds are exercised against the documents they will actually judge.
"""

from __future__ import annotations

from typing import Any

from twfi.eval.sources import (
    MIN_TABLES_WITH_UNIT,
    DocumentCapability,
    coverage,
    derive_capability,
)


def cap(**overrides: Any) -> DocumentCapability:
    """A healthy filing, shaped like 2330-FY2023-AR."""
    base: dict[str, Any] = {
        "doc_id": "2330-FY2023-AR",
        "verdict": "usable",
        "legible_pages": 337,
        "tables": 193,
        "tables_with_unit": 30,
        "labelled_charts": 33,
        "statements": "readable",
    }
    base.update(overrides)
    return derive_capability(**base)


# ------------------------------------------------- the verdict gate (bug 1)


def test_a_healthy_filing_sources_every_answerable_type() -> None:
    capability = cap()
    assert capability.sources == (
        "chart_value_trend",
        "cross_document",
        "cross_page",
        "cross_period_comparison",
        "narrative_fact",
        "numeric_calculation",
        "table_cell",
    )
    assert capability.notes == ()


def test_an_unreadable_filing_sources_nothing_despite_its_counts() -> None:
    """2317-FY2024-AR really does yield 66 tables and 47 labelled charts.

    It also yields zero legible pages. The extractors run happily over a broken text
    layer and return mojibake grids and unlabelled artwork; the first version counted
    those as capability and handed the document two question types.
    """
    capability = cap(
        doc_id="2317-FY2024-AR",
        verdict="unusable_text_layer",
        legible_pages=0,
        tables=66,
        tables_with_unit=0,
        labelled_charts=47,
        statements="absent_by_design",
    )
    assert capability.sources == ()
    assert "artefacts, not evidence" in capability.notes[0]


def test_a_partially_broken_filing_also_sources_nothing() -> None:
    """2317-FY2023-AR: 149 legible pages of 705, and 513 extracted tables.

    The protocol declares it unusable. A capability derivation that ignored the verdict
    gave it all seven types, which would have put locked questions on a filing whose
    headings extract as glyph codes.
    """
    capability = cap(
        doc_id="2317-FY2023-AR",
        verdict="partially_unusable_text_layer",
        legible_pages=149,
        tables=513,
        tables_with_unit=4,
        labelled_charts=144,
    )
    assert capability.sources == ()


def test_a_too_short_filing_sources_nothing() -> None:
    assert cap(verdict="too_short", legible_pages=5).sources == ()


# ------------------------------------------ absent vs unreadable (bug 2)


def test_statements_absent_by_design_is_not_a_defect() -> None:
    """2330-FY2024-AR embeds no statements because FY2024 股東會年報 do not (D-012).

    Conflating that with an unreadable text layer described Taiwanese filing practice
    as a defect in one PDF.
    """
    capability = cap(
        doc_id="2330-FY2024-AR",
        legible_pages=91,
        tables=55,
        tables_with_unit=10,
        labelled_charts=40,
        statements="absent_by_design",
    )
    assert "no financial statements at all" in capability.notes[0]
    assert "Not a defect" in capability.notes[0]
    assert capability.can_source("narrative_fact")
    assert capability.can_source("chart_value_trend")


def test_image_only_statements_are_named_as_such() -> None:
    """2330-FY2024-FS: the statements are there, with no text layer, for 9 pages."""
    capability = cap(
        doc_id="2330-FY2024-FS",
        legible_pages=113,
        tables=65,
        tables_with_unit=3,
        labelled_charts=20,
        statements="image_only",
        image_only_runs=[(7, 15)],
    )
    note = " ".join(capability.notes)
    assert "present but have no text layer" in note
    assert "9 consecutive pages" in note
    assert "must come from a note" in note
    assert capability.statement_pages_readable is False


def test_readable_statements_add_no_note() -> None:
    assert cap(statements="readable").notes == ()


# ------------------------------------------------- the unit gate (bug 3)


def test_a_filing_with_few_unit_bearing_tables_cannot_source_numeric() -> None:
    """The real 2330-FY2024-FS: 65 tables, 3 of them declaring a unit.

    Reusing the plain table threshold qualified it on three, which cannot yield five
    distinct numeric questions without asking the same table repeatedly.
    """
    capability = cap(tables=65, tables_with_unit=3, statements="image_only")
    assert capability.can_source("table_cell")
    assert not capability.can_source("numeric_calculation")
    assert not capability.can_source("cross_period_comparison")
    assert f"(<{MIN_TABLES_WITH_UNIT})" in " ".join(capability.notes)


def test_the_unit_threshold_is_a_boundary_not_a_suggestion() -> None:
    assert not cap(tables_with_unit=MIN_TABLES_WITH_UNIT - 1).can_source("numeric_calculation")
    assert cap(tables_with_unit=MIN_TABLES_WITH_UNIT).can_source("numeric_calculation")


def test_too_few_tables_removes_the_table_and_numeric_types() -> None:
    capability = cap(tables=2, tables_with_unit=2)
    assert not capability.can_source("table_cell")
    assert not capability.can_source("numeric_calculation")
    assert capability.can_source("narrative_fact")


# --------------------------------------------------------------- other gates


def test_unlabelled_charts_cannot_ground_a_value_question() -> None:
    """D-014: a schematic with no numbers has no value to read off it."""
    capability = cap(labelled_charts=1)
    assert not capability.can_source("chart_value_trend")
    assert "cannot ground a value question" in " ".join(capability.notes)


def test_too_few_legible_pages_removes_narrative_and_cross_page() -> None:
    capability = cap(legible_pages=9)
    assert not capability.can_source("narrative_fact")
    assert not capability.can_source("cross_page")
    assert capability.can_source("table_cell")


def test_cross_document_needs_both_prose_and_tables() -> None:
    assert not cap(legible_pages=2).can_source("cross_document")
    assert not cap(tables=1).can_source("cross_document")


def test_unanswerable_is_never_a_document_capability() -> None:
    """Whether a question is unanswerable is a property of the question.

    A filing that can source nothing else is the worst place to look for one: the
    refusal would be correct for the wrong reason.
    """
    for verdict in ("usable", "unusable_text_layer"):
        assert "unanswerable" not in cap(verdict=verdict).sources


# ----------------------------------------------------------------- coverage


def test_coverage_inverts_the_mapping() -> None:
    healthy = cap()
    broken = cap(doc_id="2317-FY2024-AR", verdict="unusable_text_layer")
    found = coverage({"2330-FY2023-AR": healthy, "2317-FY2024-AR": broken})
    assert found["narrative_fact"] == ["2330-FY2023-AR"]
    assert "2317-FY2024-AR" not in found.get("table_cell", [])


def test_a_question_type_no_document_can_source_is_absent_from_coverage() -> None:
    """An empty entry is a finding about the corpus, to be stated before annotation."""
    thin = cap(labelled_charts=0)
    found = coverage({"2330-FY2023-AR": thin})
    assert "chart_value_trend" not in found
