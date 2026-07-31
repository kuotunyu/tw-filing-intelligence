"""The protocol exists twice -- as prose and as code. They must agree.

Anti-drift is the whole point: if someone edits a threshold in the markdown but
not in ``twfi.protocol`` (or the reverse), these tests fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twfi.paths import RepoPaths
from twfi.protocol import (
    BASELINE_FACTOR,
    CANDIDATE_FACTOR,
    CHALLENGER_ITEMS,
    CHALLENGER_SWITCH_MIN_GAIN_PP,
    COMPANIES,
    DECLARED_DOCUMENTS,
    DEV_COMPANY_CODES,
    DEV_TOTAL,
    FACTOR_IDS,
    GATES,
    HARD_CATEGORIES,
    LOCKED_COMPANY_CODES,
    LOCKED_TOTAL,
    LOCKED_TYPE_COUNTS,
    POOLED_HARD_SIZE,
    PROBE_COUNT,
    ROUTE_BY_QUESTION_TYPE,
    SINGLE_GATE_CATEGORIES,
    USABLE_DOCUMENTS,
    consistency_problems,
    split_for_company,
)


@pytest.fixture()
def protocol_doc(repo_root: Path) -> str:
    return RepoPaths(root=repo_root).protocol_doc.read_text(encoding="utf-8")


# ------------------------------------------------------------------ internal consistency


def test_locked_counts_sum_to_the_declared_total() -> None:
    assert sum(LOCKED_TYPE_COUNTS.values()) == LOCKED_TOTAL == 33


def test_locked_set_meets_the_minimum_size_required_by_the_brief() -> None:
    assert LOCKED_TOTAL >= 30


def test_dev_set_is_within_the_declared_range() -> None:
    assert 12 <= DEV_TOTAL <= 18


def test_pooled_hard_size_is_the_sum_of_hard_categories() -> None:
    assert POOLED_HARD_SIZE == sum(LOCKED_TYPE_COUNTS[t] for t in HARD_CATEGORIES) == 18


def test_every_question_type_has_a_gold_route() -> None:
    assert set(ROUTE_BY_QUESTION_TYPE) == set(LOCKED_TYPE_COUNTS)


def test_unanswerable_maps_to_the_refusal_route() -> None:
    assert ROUTE_BY_QUESTION_TYPE["unanswerable"] == "unanswerable"


def test_numeric_types_route_to_the_sql_path() -> None:
    assert ROUTE_BY_QUESTION_TYPE["numeric_calculation"] == "numeric"
    assert ROUTE_BY_QUESTION_TYPE["cross_period_comparison"] == "numeric"


def test_factor_ladder_is_f0_through_f7() -> None:
    assert FACTOR_IDS == ("F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7")
    assert BASELINE_FACTOR == "F0"
    assert CANDIDATE_FACTOR == "F7"


# ------------------------------------------------------------------------- companies


def test_dev_and_locked_companies_are_disjoint() -> None:
    assert not DEV_COMPANY_CODES & LOCKED_COMPANY_CODES


def test_study_covers_at_least_three_companies_and_two_industries() -> None:
    assert len(COMPANIES) >= 3
    assert len({c.industry for c in COMPANIES}) >= 2


def test_study_covers_at_least_two_fiscal_years() -> None:
    years = {year for company in COMPANIES for year in company.fiscal_years}
    assert len(years) >= 2


def test_document_count_is_within_the_five_to_ten_range() -> None:
    total = len(DECLARED_DOCUMENTS)
    assert 5 <= total <= 10, f"the brief asks for 5-10 documents, got {total}"


def test_declared_documents_cover_every_company_year() -> None:
    declared = {(document.company_code, document.fiscal_year) for document in DECLARED_DOCUMENTS}
    expected = {(company.code, year) for company in COMPANIES for year in company.fiscal_years}
    assert declared == expected


def test_doc_ids_encode_company_year_and_kind() -> None:
    ids = {document.doc_id for document in DECLARED_DOCUMENTS}
    assert "2330-FY2023-AR" in ids
    assert "2330-FY2024-FS" in ids
    assert len(ids) == len(DECLARED_DOCUMENTS), "doc_ids must be unique"


def test_financial_reports_exist_only_for_fy2024() -> None:
    """FY2023 annual reports still embed their statements; FY2024 ones do not."""
    statements = [d for d in DECLARED_DOCUMENTS if d.doc_type == "financial_report"]
    assert statements
    assert {d.fiscal_year for d in statements} == {2024}
    assert {d.company_code for d in statements} == LOCKED_COMPANY_CODES


def test_the_unreadable_filings_stay_declared_but_unusable() -> None:
    """Deleting the records would delete the finding; usable=False excludes them instead."""
    unusable = {d.doc_id: d for d in DECLARED_DOCUMENTS if not d.usable}
    assert set(unusable) == {"2317-FY2023-AR", "2317-FY2024-AR"}
    for document in unusable.values():
        assert "readable" in document.note or "ToUnicode" in document.note
        assert document not in USABLE_DOCUMENTS
    assert len(USABLE_DOCUMENTS) == len(DECLARED_DOCUMENTS) - 2


def test_the_scale_of_the_extraction_failure_is_recorded() -> None:
    """Two of seven annual reports, both from one issuer: a headline finding."""
    annual = [d for d in DECLARED_DOCUMENTS if d.doc_type == "annual_report"]
    assert len(annual) == 7
    broken = [d for d in annual if not d.usable]
    assert len(broken) == 2
    assert {d.company_code for d in broken} == {"2317"}, "the failure is issuer-specific"
    statements = [d for d in DECLARED_DOCUMENTS if d.doc_type == "financial_report"]
    assert all(d.usable for d in statements), "every 財務報告書 extracted cleanly"


def test_narrative_evidence_still_spans_two_industries() -> None:
    """Losing 2317 and 1301 to encoding must not collapse the industry coverage."""
    narrative_codes = {d.company_code for d in USABLE_DOCUMENTS if d.doc_type == "annual_report"}
    industries = {c.industry for c in COMPANIES if c.code in narrative_codes}
    assert len(industries) >= 2, f"narrative evidence covers only {industries}"


def test_locked_numeric_evidence_covers_both_fiscal_years() -> None:
    """Cross-period questions need statements from more than one year."""
    years = {
        d.fiscal_year
        for d in USABLE_DOCUMENTS
        if d.split == "locked" and (d.doc_type == "financial_report" or d.fiscal_year == 2023)
    }
    assert {2023, 2024} <= years


def test_a_structurally_different_industry_is_included() -> None:
    """A financial holding company keeps the study off easy-layout filings only."""
    assert any("金融" in company.industry for company in COMPANIES)


@pytest.mark.parametrize(
    ("code", "expected"), [("2412", "dev"), ("1301", "dev"), ("2330", "locked")]
)
def test_split_lookup(code: str, expected: str) -> None:
    assert split_for_company(code) == expected


def test_split_lookup_rejects_unknown_companies() -> None:
    with pytest.raises(KeyError):
        split_for_company("9999")


# ------------------------------------------------------ agreement with the markdown


def test_every_company_appears_in_the_protocol_document(protocol_doc: str) -> None:
    for company in COMPANIES:
        assert company.code in protocol_doc
        assert company.name in protocol_doc


def test_every_locked_type_count_appears_in_the_protocol_table(protocol_doc: str) -> None:
    for question_type, count in LOCKED_TYPE_COUNTS.items():
        assert f"| `{question_type}` | {count} |" in protocol_doc, (
            f"{question_type} count disagrees between twfi.protocol and the markdown"
        )


def test_gate_thresholds_appear_in_the_protocol_document(protocol_doc: str) -> None:
    assert "≥ 10 個百分點" in protocol_doc  # G2
    assert "5 個百分點" in protocol_doc  # G3
    assert "≥ 90%" in protocol_doc  # G4/G5
    assert "≥ 85%" in protocol_doc  # G6
    assert "≤ 25%" in protocol_doc  # G7
    assert "22 GB" in protocol_doc  # G10


def test_probe_and_challenger_counts_appear_in_the_protocol(protocol_doc: str) -> None:
    assert f"{PROBE_COUNT} 個 no-evidence probe" in protocol_doc
    assert f"**{CHALLENGER_ITEMS} 個**" in protocol_doc


# ----------------------------------------------------------------------------- gates


def test_gate_values_match_the_pre_registered_numbers() -> None:
    assert GATES.pooled_hard_min_gain_pp == 10.0
    assert GATES.single_hard_min_gain_pp == 10.0
    assert GATES.max_overall_regression_pp == 5.0
    assert GATES.min_citation_validity == 0.90
    assert GATES.min_numeric_route_accuracy == 0.90
    assert GATES.min_route_accuracy == 0.85
    assert GATES.max_over_answer_rate == 0.25
    assert GATES.min_refusal_precision == 0.80
    assert GATES.min_probe_refusals == 4
    assert GATES.max_retrieval_p95_s == 3.0
    assert GATES.max_generation_p95_s == 60.0
    assert GATES.max_vram_peak_gb == 22.0


def test_gates_are_immutable() -> None:
    with pytest.raises(AttributeError):
        GATES.min_route_accuracy = 0.5  # type: ignore[misc]


def test_challenger_needs_a_clear_margin() -> None:
    assert CHALLENGER_SWITCH_MIN_GAIN_PP == 10.0


# ------------------------------------------------------------- consistency checker


def test_the_real_protocol_is_self_consistent() -> None:
    assert consistency_problems() == []


def test_detects_a_type_count_that_does_not_sum() -> None:
    broken = dict(LOCKED_TYPE_COUNTS) | {"narrative_fact": 99}
    problems = consistency_problems(type_counts=broken)
    assert any("sum to" in p for p in problems)


def test_detects_a_missing_question_type() -> None:
    broken = {k: v for k, v in LOCKED_TYPE_COUNTS.items() if k != "unanswerable"}
    problems = consistency_problems(type_counts=broken)
    assert any("cover exactly the QuestionType literals" in p for p in problems)


def test_detects_a_hard_category_that_is_not_a_question_type() -> None:
    problems = consistency_problems(hard_categories={"not_a_type"})
    assert any("must all be question types" in p for p in problems)


def test_detects_overlapping_splits() -> None:
    """The failure mode this whole study is designed to avoid."""
    problems = consistency_problems(dev_codes={"2330"}, locked_codes={"2330"})
    assert any("overlap: ['2330']" in p for p in problems)


def test_detects_a_missing_gold_route() -> None:
    broken = {k: v for k, v in ROUTE_BY_QUESTION_TYPE.items() if k != "cross_page"}
    problems = consistency_problems(routes=broken)
    assert any("needs a gold route" in p for p in problems)


def test_detects_an_unknown_gold_route() -> None:
    broken = dict(ROUTE_BY_QUESTION_TYPE) | {"cross_page": "telepathy"}
    problems = consistency_problems(routes=broken)
    assert any("unknown gold routes: ['telepathy']" in p for p in problems)


# ----------------------------------------------------------------- small-sample


def test_one_item_in_the_smallest_hard_category_exceeds_the_single_category_gate() -> None:
    """Why G2 is judged on the pooled set: small n makes a single item look huge."""
    smallest = min(LOCKED_TYPE_COUNTS[t] for t in SINGLE_GATE_CATEGORIES)
    one_item_pp = 100.0 / smallest
    assert one_item_pp > GATES.single_hard_min_gain_pp
    pooled_items_needed = GATES.pooled_hard_min_gain_pp / 100.0 * POOLED_HARD_SIZE
    assert pooled_items_needed > 2, "the pooled gate must need more than two extra answers"


def test_the_pooled_threshold_tracks_the_pool_it_is_judged_on() -> None:
    """This test caught a real weakening, not a stale number.

    Cutting chart_value_trend from five items to two shrank the pooled hard set from 21
    to 18, and at the old 10 points a gain of 1.8 items would have passed -- so two extra
    correct answers could clear a gate written to need more than two. The threshold moved
    to 15, which needs three. A threshold may only ever move in the direction that makes
    GO harder.
    """
    assert GATES.pooled_hard_min_gain_pp == 15.0
    needed = GATES.pooled_hard_min_gain_pp / 100.0 * POOLED_HARD_SIZE
    assert 2 < needed <= 3


def test_chart_cannot_satisfy_the_single_category_gate_alone() -> None:
    """At two items one answer is fifty points, which no threshold can survive.

    chart_value_trend stays in the pooled set, where it contributes evidence without being
    able to decide anything by itself.
    """
    assert "chart_value_trend" in HARD_CATEGORIES
    assert "chart_value_trend" not in SINGLE_GATE_CATEGORIES
    assert 100.0 / LOCKED_TYPE_COUNTS["chart_value_trend"] == 50.0
