"""The gold schema's job is to make an unfounded answer unrepresentable.

Most of these tests are about refusal: what the schema will *not* accept. A validator
that has only ever been seen passing is not evidence of anything.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from typing import Any, get_args

import pytest

from twfi.eval.gold import (
    ALLOWED_UNITS,
    AnswerProvenance,
    BBoxRef,
    CompanyRef,
    DraftItem,
    EvidenceRef,
    GoldRecord,
    RefusalReasonClass,
    StructuredSourceKey,
    Tolerance,
    default_tolerance,
    gold_route,
    load_gold,
    parse_record,
    record_problems,
    set_problems,
)
from twfi.protocol import LOCKED_TYPE_COUNTS

TODAY = dt.date(2026, 7, 31)


def make(**overrides: Any) -> GoldRecord:
    """A valid locked narrative record, with fields overridable per test."""
    base: dict[str, Any] = {
        "question_id": "LOCK-0001",
        "question_type": "narrative_fact",
        "question": "台積電 2023 年報如何描述其先進封裝產能規劃？",
        "answer": "說明將擴充 CoWoS 產能以支援高效能運算需求。",
        "company": CompanyRef("台積電", "2330"),
        "period": "FY2023",
        "source_document": ("2330-FY2023-AR",),
        "required_evidence": (EvidenceRef("page", "2330-FY2023-AR#p120"),),
        "answer_provenance": "human_read_pdf",
        "annotated_at": TODAY,
        "page_numbers": (120,),
    }
    base.update(overrides)
    return GoldRecord(**base)


def payload_of(record: GoldRecord, **overrides: Any) -> dict[str, Any]:
    """Serialise a record the way a gold.jsonl line would carry it."""
    body: dict[str, Any] = {
        "question_id": record.question_id,
        "question_type": record.question_type,
        "question": record.question,
        "answer": record.answer,
        "company": {"name": record.company.name, "code": record.company.code},
        "period": record.period,
        "source_document": list(record.source_document),
        "required_evidence": [
            {"kind": item.kind, "ref": item.ref} for item in record.required_evidence
        ],
        "answer_provenance": record.answer_provenance,
        "annotated_at": record.annotated_at.isoformat(),
        "page_numbers": list(record.page_numbers),
    }
    body.update(overrides)
    return body


# --------------------------------------------------- the barrier against forgery


def test_a_machine_annotator_is_refused() -> None:
    """The single most important refusal in the codebase.

    If a gold answer could be attributed to a machine, the pre-registration would be
    decoration: the study would be grading a model against a model.
    """
    with pytest.raises(ValueError, match="annotator must be 'human'"):
        parse_record(payload_of(make(), annotator="claude-opus-5"))


@pytest.mark.parametrize("forged", ["model", "llm", "tooling", "auto", "", None])
def test_no_spelling_of_machine_authorship_is_accepted(forged: object) -> None:
    with pytest.raises(ValueError, match="annotator must be 'human'"):
        parse_record(payload_of(make(), annotator=forged))


def test_the_gold_record_type_admits_only_one_annotator() -> None:
    """Enforced by the type, not only by the parser, so no other construction path differs."""
    annotation = GoldRecord.__dataclass_fields__["annotator"].type
    assert "Literal['human']" in str(annotation).replace('"', "'")


def test_our_own_extractor_is_not_an_admissible_answer_source() -> None:
    """The circularity guard: gold must not come from the parser under test.

    A wrong extraction would become a wrong gold answer, which the candidate -- running
    the same extractor -- would reproduce and be scored correct for, making the measured
    F1/F4 gain an artefact of grading a parser against itself.
    """
    assert set(get_args(AnswerProvenance)) == {"human_read_pdf", "official_structured"}
    with pytest.raises(ValueError, match="answer_provenance must be one of"):
        parse_record(payload_of(make(), answer_provenance="extracted_table"))


def test_a_missing_provenance_is_refused_rather_than_defaulted() -> None:
    body = payload_of(make())
    del body["answer_provenance"]
    with pytest.raises(ValueError, match="answer_provenance"):
        parse_record(body)


def test_a_draft_cannot_carry_an_answer() -> None:
    """A draft is a pointer at evidence, not a gold record with a field missing."""
    names = {f.name for f in dataclasses.fields(DraftItem)}
    assert not names & {"answer", "annotator", "answer_provenance", "acceptable_variants"}


def test_an_official_structured_answer_must_name_its_row() -> None:
    problems = record_problems(make(answer_provenance="official_structured"), gold_set="locked")
    assert any("name the row it came from" in problem for problem in problems)


# ------------------------------------------------------------------ answerability


def test_a_valid_record_has_no_problems() -> None:
    assert record_problems(make(), gold_set="locked") == []


def test_an_unanswerable_question_must_have_a_null_answer() -> None:
    record = make(
        question_type="unanswerable",
        answerable=False,
        answer="事實上是有的",
        refusal_reason_class="absent_from_documents",
        required_evidence=(),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("must have answer=null" in problem for problem in problems)


def test_an_unanswerable_question_needs_a_reason_class() -> None:
    record = make(question_type="unanswerable", answerable=False, answer=None, required_evidence=())
    problems = record_problems(record, gold_set="locked")
    assert any("needs a refusal_reason_class" in problem for problem in problems)


def test_the_type_and_the_answerable_flag_must_agree() -> None:
    """Two fields encoding one fact is a place for them to disagree, so it is checked."""
    record = make(question_type="unanswerable", answerable=True, answer=None, required_evidence=())
    problems = record_problems(record, gold_set="locked")
    assert any("must agree" in problem for problem in problems)


def test_an_answerable_question_needs_a_non_empty_answer() -> None:
    problems = record_problems(make(answer="   "), gold_set="locked")
    assert any("non-empty answer" in problem for problem in problems)


def test_a_refusal_class_on_an_answerable_question_is_a_problem() -> None:
    problems = record_problems(
        make(refusal_reason_class="absent_from_documents"), gold_set="locked"
    )
    assert any("only on unanswerable" in problem for problem in problems)


# ----------------------------------------------------------------------- evidence


def test_a_numeric_answer_needs_a_row_or_a_bbox() -> None:
    record = make(
        question_id="LOCK-0002",
        question_type="table_cell",
        question="2330 FY2023 合併資產負債表的現金及約當現金為多少？",
        answer="1,465,427,753",
        unit="千元",
        currency="TWD",
        required_evidence=(EvidenceRef("table_cell", "2330-FY2023-AR#p200/t1/r4/c2"),),
        tolerance=Tolerance("relative", 0.005),
        page_numbers=(200,),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("structured_source_key or a bbox" in problem for problem in problems)


def test_a_chart_question_needs_crop_evidence_not_just_a_page() -> None:
    """Scoring crop-level citation is the point of the chart route."""
    record = make(
        question_type="chart_value_trend",
        answer="約 2,894 億元",
        unit="億元",
        currency="TWD",
        tolerance=Tolerance("relative", 0.005),
        bbox=(BBoxRef(120, (72.0, 300.0, 500.0, 560.0)),),
        required_evidence=(EvidenceRef("page", "2330-FY2023-AR#p120"),),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("chart_crop" in problem for problem in problems)


def test_a_numeric_question_needs_an_explicit_tolerance() -> None:
    record = make(
        question_type="numeric_calculation",
        answer="66.25",
        unit="%",
        required_evidence=(EvidenceRef("sql_row", "fin_line_item/2330/FY2026Q1/營業收入"),),
        structured_source_key=StructuredSourceKey(
            "fin_line_item", "2330|FY2026Q1|營業毛利（毛損）"
        ),
        answer_provenance="official_structured",
    )
    problems = record_problems(record, gold_set="locked")
    assert any("needs an explicit tolerance" in problem for problem in problems)


def test_a_bbox_page_must_be_listed_in_page_numbers() -> None:
    """Otherwise the citation check and the evidence set disagree about the page."""
    problems = record_problems(
        make(bbox=(BBoxRef(999, (10.0, 10.0, 20.0, 20.0)),)), gold_set="locked"
    )
    assert any("not listed in page_numbers" in problem for problem in problems)


def test_page_evidence_requires_page_numbers() -> None:
    problems = record_problems(make(page_numbers=()), gold_set="locked")
    assert any("requires page_numbers" in problem for problem in problems)


def test_a_cross_page_question_must_cite_two_pages() -> None:
    record = make(question_type="cross_page", page_numbers=(120,))
    problems = record_problems(record, gold_set="locked")
    assert any("at least two pages" in problem for problem in problems)


def test_a_cross_document_question_must_cite_two_documents() -> None:
    record = make(question_type="cross_document")
    problems = record_problems(record, gold_set="locked")
    assert any("at least two documents" in problem for problem in problems)


def test_a_cross_period_question_needs_a_span_period() -> None:
    record = make(
        question_type="cross_period_comparison",
        period="FY2024",
        answer="成長 33.89%",
        unit="%",
        tolerance=Tolerance("absolute", 0.1),
        required_evidence=(EvidenceRef("table_cell", "2330-FY2023-AR#p200/t1/r4/c2"),),
        structured_source_key=StructuredSourceKey("fin_line_item", "2330|FY2023|營業收入"),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("span period" in problem for problem in problems)


def test_required_evidence_cannot_be_empty_for_an_answerable_question() -> None:
    problems = record_problems(make(required_evidence=()), gold_set="locked")
    assert any("complete-evidence metric" in problem for problem in problems)


# ------------------------------------------------------------------------ sources


def test_an_unusable_document_cannot_be_a_source() -> None:
    """2317's annual reports have a broken text layer, so no question may rest on them."""
    record = make(
        company=CompanyRef("鴻海", "2317"),
        source_document=("2317-FY2023-AR",),
        required_evidence=(EvidenceRef("page", "2317-FY2023-AR#p10"),),
        page_numbers=(10,),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("not a declared usable document" in problem for problem in problems)


def test_a_document_belonging_to_another_company_is_a_problem() -> None:
    record = make(company=CompanyRef("鴻海", "2317"), source_document=("2330-FY2023-AR",))
    problems = record_problems(record, gold_set="locked")
    assert any("belongs to 2330" in problem for problem in problems)


def test_a_dev_company_may_not_appear_in_the_locked_set() -> None:
    """The split is the study's only defence against tuning on the locked data."""
    record = make(
        company=CompanyRef("中華電信", "2412"),
        source_document=("2412-FY2023-AR",),
        required_evidence=(EvidenceRef("page", "2412-FY2023-AR#p30"),),
        page_numbers=(30,),
    )
    problems = record_problems(record, gold_set="locked")
    assert any("is a dev company" in problem for problem in problems)


def test_a_locked_company_may_not_appear_in_the_dev_set() -> None:
    problems = record_problems(make(question_id="DEV-0001"), gold_set="dev")
    assert any("is a locked company" in problem for problem in problems)


def test_a_company_outside_the_study_is_refused() -> None:
    record = make(company=CompanyRef("聯發科", "2454"), source_document=("2330-FY2023-AR",))
    problems = record_problems(record, gold_set="locked")
    assert any("not a study company" in problem for problem in problems)


def test_the_id_prefix_must_match_the_set() -> None:
    problems = record_problems(make(question_id="DEV-0001"), gold_set="locked")
    assert any("must start with LOCK-" in problem for problem in problems)


# -------------------------------------------------------------------------- units


def test_a_non_canonical_unit_spelling_is_corrected_not_accepted() -> None:
    """仟元 and 千元 must not both circulate, or a unit check will fire on a non-error."""
    record = make(unit="仟元", currency="TWD")
    problems = record_problems(record, gold_set="locked")
    assert any("should be written '千元'" in problem for problem in problems)


def test_an_unknown_unit_is_refused() -> None:
    problems = record_problems(make(unit="桶"), gold_set="locked")
    assert any("not an allowed unit" in problem for problem in problems)


def test_a_monetary_answer_must_state_its_currency() -> None:
    """A figure in 千元 with no currency is how a TWD number gets read as USD."""
    problems = record_problems(make(unit="千元", currency=None), gold_set="locked")
    assert any("must state its currency" in problem for problem in problems)


def test_a_percentage_needs_no_currency() -> None:
    assert record_problems(make(unit="%", currency=None), gold_set="locked") == []


def test_the_allowed_units_share_the_numeric_layers_scales() -> None:
    assert {"元", "千元", "百萬元", "億元"} <= ALLOWED_UNITS


# ---------------------------------------------------------------------- tolerance


def test_percentages_get_an_absolute_tolerance() -> None:
    """0.5% of a 0.4pp margin is 0.002pp, which no honest extraction would hit."""
    assert default_tolerance("%") == Tolerance("absolute", 0.1)


@pytest.mark.parametrize("unit", ["千元", "元", "倍", None])
def test_everything_else_gets_the_relative_default(unit: str | None) -> None:
    assert default_tolerance(unit) == Tolerance("relative", 0.005)


def test_a_relative_tolerance_scales_with_the_figure() -> None:
    tolerance = Tolerance("relative", 0.005)
    assert tolerance.accepts(gold=1_000_000.0, candidate=1_004_000.0)
    assert not tolerance.accepts(gold=1_000_000.0, candidate=1_006_000.0)


def test_an_absolute_tolerance_is_in_the_answers_own_unit() -> None:
    tolerance = Tolerance("absolute", 0.1)
    assert tolerance.accepts(gold=66.25, candidate=66.3)
    assert not tolerance.accepts(gold=66.25, candidate=66.5)


def test_a_zero_tolerance_is_refused() -> None:
    """Exact float equality on an extracted figure is a gate nothing would pass."""
    with pytest.raises(ValueError, match="must be positive"):
        Tolerance("relative", 0.0)


# ----------------------------------------------------------------- value objects


def test_a_bbox_needs_positive_area() -> None:
    with pytest.raises(ValueError, match="positive area"):
        BBoxRef(1, (100.0, 100.0, 100.0, 200.0))


def test_page_numbers_are_one_based() -> None:
    with pytest.raises(ValueError, match="1-based"):
        BBoxRef(0, (1.0, 1.0, 2.0, 2.0))


def test_a_structured_key_needs_both_halves() -> None:
    with pytest.raises(ValueError, match="both a table and a row key"):
        StructuredSourceKey("fin_line_item", "  ")


def test_evidence_needs_a_reference() -> None:
    with pytest.raises(ValueError, match="needs a reference"):
        EvidenceRef("page", "")


# ------------------------------------------------------------------ set-level rules


def _locked_set() -> list[GoldRecord]:
    """A locked set with the exact pre-registered composition."""
    records: list[GoldRecord] = []
    counter = 0
    causes = list(get_args(RefusalReasonClass))
    for qtype, count in LOCKED_TYPE_COUNTS.items():
        for index in range(count):
            counter += 1
            records.append(_locked_item(qtype, counter, index, causes))
    return records


def _locked_item(qtype: str, counter: int, index: int, causes: list[str]) -> GoldRecord:
    common: dict[str, Any] = {
        "question_id": f"LOCK-{counter:04d}",
        "question_type": qtype,
        "question": f"問題 {counter}：{qtype} 的第 {index + 1} 題？",
        "company": CompanyRef("台積電", "2330"),
        "period": "FY2023",
        "source_document": ("2330-FY2023-AR",),
        "answer_provenance": "human_read_pdf",
        "annotated_at": TODAY,
        "page_numbers": (100 + counter, 200 + counter),
    }
    if qtype == "unanswerable":
        return GoldRecord(
            **common,
            answer=None,
            answerable=False,
            refusal_reason_class=causes[index % len(causes)],  # type: ignore[arg-type]
            required_evidence=(),
        )
    if qtype == "cross_document":
        common["source_document"] = ("2330-FY2023-AR", "2330-FY2024-FS")
    if qtype == "cross_period_comparison":
        common["period"] = "FY2023-FY2024"

    kind = sorted(_kind_for(qtype))[0]
    extra: dict[str, Any] = {}
    if qtype in {"table_cell", "numeric_calculation", "cross_period_comparison"}:
        extra["tolerance"] = Tolerance("relative", 0.005)
        extra["structured_source_key"] = StructuredSourceKey("fin_line_item", f"row-{counter}")
    if qtype == "chart_value_trend":
        extra["tolerance"] = Tolerance("relative", 0.005)
        extra["bbox"] = (BBoxRef(100 + counter, (72.0, 300.0, 500.0, 560.0)),)
    return GoldRecord(
        **common,
        answer=f"答案 {counter}",
        required_evidence=(EvidenceRef(kind, f"ref-{counter}"),),  # type: ignore[arg-type]
        **extra,
    )


def _kind_for(qtype: str) -> frozenset[str]:
    from twfi.eval.gold import REQUIRED_EVIDENCE_KINDS

    return REQUIRED_EVIDENCE_KINDS[qtype] or frozenset({"page"})


def test_a_complete_locked_set_validates() -> None:
    assert set_problems(_locked_set(), gold_set="locked") == []


def test_the_locked_set_must_match_the_pre_registered_distribution() -> None:
    """The mix is fixed before annotation so it cannot tilt toward what the system does well."""
    records = _locked_set()
    short = [r for r in records if r.question_type != "narrative_fact"]
    problems = set_problems(short, gold_set="locked")
    assert any("needs 6 narrative_fact questions, has 0" in problem for problem in problems)


def test_the_locked_unanswerable_questions_must_cover_every_cause() -> None:
    records = [
        r
        for r in _locked_set()
        if r.question_type != "unanswerable" or r.refusal_reason_class == "absent_from_documents"
    ]
    problems = set_problems(records, gold_set="locked")
    assert any("must cover every cause" in problem for problem in problems)


def test_a_duplicate_question_id_is_caught() -> None:
    first = make(question_id="LOCK-0001")
    second = make(question_id="LOCK-0001", question="另一個問題？")
    problems = set_problems([first, second], gold_set="dev", type_counts={})
    assert any("duplicate question_id LOCK-0001" in problem for problem in problems)


def test_the_same_question_asked_twice_is_caught() -> None:
    """Two ids for one question would let a single failure count twice."""
    first = make(question_id="LOCK-0001")
    second = make(
        question_id="LOCK-0002", question="  台積電 2023 年報如何描述其先進封裝產能規劃？ "
    )
    problems = set_problems([first, second], gold_set="locked", type_counts={})
    assert any("repeats the question text" in problem for problem in problems)


def test_the_dev_set_composition_is_not_constrained() -> None:
    """Dev exists to be adjusted; only the locked mix is pre-registered."""
    assert (
        set_problems(
            [
                make(
                    question_id="DEV-0001",
                    company=CompanyRef("中華電信", "2412"),
                    source_document=("2412-FY2023-AR",),
                    required_evidence=(EvidenceRef("page", "2412-FY2023-AR#p30"),),
                    page_numbers=(30,),
                )
            ],
            gold_set="dev",
        )
        == []
    )


# ------------------------------------------------------------------------ loading


def test_a_jsonl_file_round_trips() -> None:
    line = json.dumps(payload_of(make()), ensure_ascii=False)
    (record,) = load_gold([line])
    assert record.question_id == "LOCK-0001"
    assert record.annotator == "human"


def test_blank_and_commented_lines_are_skipped() -> None:
    line = json.dumps(payload_of(make()), ensure_ascii=False)
    assert len(load_gold(["", "  ", "// a note from the annotator", line])) == 1


def test_a_broken_line_is_reported_with_its_number() -> None:
    good = json.dumps(payload_of(make()), ensure_ascii=False)
    with pytest.raises(ValueError, match="line 2 is not valid JSON"):
        load_gold([good, "{not json"])


def test_a_bad_record_is_reported_with_its_line_number() -> None:
    good = json.dumps(payload_of(make()), ensure_ascii=False)
    bad = json.dumps(payload_of(make(), annotator="model"), ensure_ascii=False)
    with pytest.raises(ValueError, match="line 2: annotator must be 'human'"):
        load_gold([good, bad])


def test_an_unknown_field_is_refused() -> None:
    """A typo in a field name would otherwise be silently ignored."""
    with pytest.raises(ValueError, match="unknown gold fields"):
        parse_record(payload_of(make(), tolerence={"type": "relative", "value": 0.005}))


def test_a_missing_required_field_names_itself() -> None:
    body = payload_of(make())
    del body["period"]
    with pytest.raises(ValueError, match="missing required field 'period'"):
        parse_record(body)


# -------------------------------------------------------------------------- routes


def test_every_question_type_maps_to_a_route() -> None:
    for qtype in LOCKED_TYPE_COUNTS:
        assert gold_route(qtype)


def test_a_table_cell_question_is_graded_on_the_chart_route() -> None:
    """Tabular evidence resolves through the same structured path as figures."""
    assert gold_route("table_cell") == "chart"
    assert gold_route("unanswerable") == "unanswerable"
