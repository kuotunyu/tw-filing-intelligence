from __future__ import annotations

import datetime as dt
from pathlib import Path

import pymupdf

from twfi.chart.crop_answer import ChartAnswer
from twfi.eval.citations import CitationGrader
from twfi.eval.gold import (
    BBoxRef,
    CompanyRef,
    EvidenceRef,
    GoldRecord,
    StructuredSourceKey,
)
from twfi.index.retrieve import Hit
from twfi.numeric.route import NumericAnswer

DOC_ID = "1301-FY2023-AR"


def _pdf(path: Path, text: str = "資產總計 530,738,356 112年 63%") -> Path:
    document = pymupdf.open()
    page = document.new_page(width=300, height=200)
    page.insert_text((60, 80), text, fontname="china-ts", fontsize=11)
    document.save(path)
    document.close()
    return path


def _record(
    *,
    question_type: str = "table_cell",
    answer: str = "530,738,356",
    required_evidence: tuple[EvidenceRef, ...] | None = None,
    bbox: tuple[BBoxRef, ...] = (),
    structured_source_key: StructuredSourceKey | None = None,
) -> GoldRecord:
    return GoldRecord(
        question_id="DEV-TEST",
        question_type=question_type,  # type: ignore[arg-type]
        question="台塑民國112年度的資產總計是多少？",
        answer=answer,
        company=CompanyRef("台塑", "1301"),
        period="FY2023",
        source_document=(DOC_ID,),
        required_evidence=required_evidence
        or (EvidenceRef("table_cell", f"{DOC_ID}#p1/資產總計/112年度"),),
        answer_provenance="human_read_pdf",
        annotated_at=dt.date(2026, 8, 1),
        page_numbers=(1,),
        bbox=bbox,
        structured_source_key=structured_source_key,
    )


def _hit(text: str = "資產總計 530,738,356") -> Hit:
    return Hit(
        chunk_index=0,
        score=1.0,
        chunk_id="chunk-1",
        doc_id=DOC_ID,
        pages=(1,),
        text=text,
    )


def test_narrative_citation_resolves_and_supports_the_answer(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})

    verdict = grader.grade(
        record=_record(),
        predicted="530,738,356",
        cited=(1,),
        passages=(_hit(),),
        refused=False,
    )

    assert verdict.valid is True


def test_out_of_range_citation_is_invalid_instead_of_being_dropped(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})

    verdict = grader.grade(
        record=_record(),
        predicted="530,738,356",
        cited=(2,),
        passages=(_hit(),),
        refused=False,
    )

    assert verdict.valid is False
    assert "out of range" in verdict.detail


def test_answer_without_any_citation_is_invalid(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})

    verdict = grader.grade(
        record=_record(),
        predicted="530,738,356",
        cited=(),
        passages=(_hit(),),
        refused=False,
    )

    assert verdict.valid is False
    assert "no citation" in verdict.detail


def test_numeric_citation_resolves_the_registered_structured_row(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})
    row_key = f"{DOC_ID}|p1|資產總計|112年度"
    numeric = NumericAnswer(
        value=530738356,
        unit="千元",
        period="FY2023",
        formula="資產總計",
        operands=("資產總計=530738356",),
        source_refs=(f"1301 FY2023 資產總計 [extracted_text_row:{row_key}]",),
    )

    verdict = grader.grade(
        record=_record(structured_source_key=StructuredSourceKey("pdf_table", row_key)),
        predicted="530,738,356",
        cited=(),
        passages=(),
        refused=False,
        numeric=numeric,
    )

    assert verdict.valid is True


def test_numeric_citation_to_a_different_row_is_invalid(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})
    expected = f"{DOC_ID}|p1|資產總計|112年度"
    numeric = NumericAnswer(
        value=530738356,
        unit="千元",
        period="FY2023",
        operands=("資產總計=530738356",),
        source_refs=(f"1301 FY2023 負債總計 [extracted_text_row:{DOC_ID}|p1|負債總計|112年度]",),
    )

    verdict = grader.grade(
        record=_record(structured_source_key=StructuredSourceKey("pdf_table", expected)),
        predicted="530,738,356",
        cited=(),
        passages=(),
        refused=False,
        numeric=numeric,
    )

    assert verdict.valid is False
    assert "structured row" in verdict.detail


def test_chart_citation_uses_the_original_page_bbox_and_visible_value(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})
    box = (50.0, 50.0, 250.0, 100.0)
    record = _record(
        question_type="chart_value_trend",
        answer="112年 63%",
        required_evidence=(EvidenceRef("chart_crop", f"{DOC_ID}#p1/年成長率"),),
        bbox=(BBoxRef(page=1, bbox=box),),
    )
    chart = ChartAnswer(
        value="112年 63%",
        unit="%",
        basis="112年列的數值標籤",
        doc_id=DOC_ID,
        crop_page=1,
        bbox=box,
        crop_ref="chart-1",
        model="qwen3.6:27b",
    )

    verdict = grader.grade(
        record=record,
        predicted=chart.value,
        cited=(),
        passages=(),
        refused=False,
        chart=chart,
    )

    assert verdict.valid is True


def test_chart_bbox_that_does_not_overlap_gold_is_invalid(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})
    record = _record(
        question_type="chart_value_trend",
        answer="112年 63%",
        required_evidence=(EvidenceRef("chart_crop", f"{DOC_ID}#p1/年成長率"),),
        bbox=(BBoxRef(page=1, bbox=(50.0, 50.0, 250.0, 100.0)),),
    )
    chart = ChartAnswer(
        value="112年 63%",
        unit="%",
        basis="112年列的數值標籤",
        doc_id=DOC_ID,
        crop_page=1,
        bbox=(0.0, 120.0, 40.0, 180.0),
        crop_ref="chart-2",
        model="qwen3.6:27b",
    )

    verdict = grader.grade(
        record=record,
        predicted=chart.value,
        cited=(),
        passages=(),
        refused=False,
        chart=chart,
    )

    assert verdict.valid is False
    assert "IoU" in verdict.detail


def test_refusal_has_no_applicable_citation_denominator(tmp_path: Path) -> None:
    grader = CitationGrader({DOC_ID: _pdf(tmp_path / "filing.pdf")})

    verdict = grader.grade(
        record=_record(),
        predicted="無法回答",
        cited=(),
        passages=(),
        refused=True,
    )

    assert verdict.valid is None
