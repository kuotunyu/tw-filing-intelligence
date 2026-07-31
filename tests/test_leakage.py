"""Each leakage vector gets a test that proves it is caught.

Company disjointness is the study's only defence against overfitting, and it has more
ways to fail than "the same code appears twice". Every one of those ways is exercised
here with input that should fail, because a check only ever seen passing is not evidence.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from twfi.eval.gold import CompanyRef, EvidenceRef, GoldRecord
from twfi.eval.leakage import (
    NEAR_DUPLICATE_THRESHOLD,
    bigrams,
    leakage_problems,
    near_duplicates,
    similarity,
)

TODAY = dt.date(2026, 7, 31)


def record(
    question_id: str,
    *,
    question: str = "本公司的營運策略為何？",
    code: str = "2330",
    name: str = "台積電",
    documents: tuple[str, ...] = ("2330-FY2023-AR",),
) -> GoldRecord:
    fields: dict[str, Any] = {
        "question_id": question_id,
        "question_type": "narrative_fact",
        "question": question,
        "answer": "答案",
        "company": CompanyRef(name, code),
        "period": "FY2023",
        "source_document": documents,
        "required_evidence": (EvidenceRef("page", f"{documents[0]}#p1"),),
        "answer_provenance": "human_read_pdf",
        "annotated_at": TODAY,
        "page_numbers": (1,),
    }
    return GoldRecord(**fields)


#: Distinct questions on purpose. Giving both sides the same default made the
#: near-duplicate check fire on the fixtures themselves -- the check working, but it
#: would have masked whatever the test was actually about.
DEV = record(
    "DEV-0001",
    question="固網與行動業務的資本支出如何分配？",
    code="2412",
    name="中華電信",
    documents=("2412-FY2023-AR",),
)
LOCKED = record("LOCK-0001", question="先進製程的產能擴充計畫如何說明？")


# ------------------------------------------------------------------- happy path


def test_a_properly_split_pair_of_sets_has_no_leakage() -> None:
    assert leakage_problems({"dev": [DEV], "locked": [LOCKED]}) == []


def test_empty_sets_report_no_leakage() -> None:
    """Absence of records is not leakage; the script, not this function, judges emptiness."""
    assert leakage_problems({"dev": [], "locked": []}) == []


# ------------------------------------------------------------ the split itself


def test_a_locked_company_in_the_dev_set_is_caught() -> None:
    problems = leakage_problems({"dev": [record("DEV-0002")], "locked": [LOCKED]})
    assert any("not on the dev side" in problem for problem in problems)


def test_a_dev_company_in_the_locked_set_is_caught() -> None:
    intruder = record("LOCK-0002", code="2412", name="中華電信", documents=("2412-FY2023-AR",))
    problems = leakage_problems({"dev": [DEV], "locked": [intruder]})
    assert any("not on the locked side" in problem for problem in problems)


def test_a_shared_company_across_sides_is_reported_once_as_an_overlap() -> None:
    problems = leakage_problems({"dev": [record("DEV-0003")], "locked": [LOCKED]})
    assert any("share companies: ['2330']" in problem for problem in problems)


def test_a_shared_document_across_sides_is_caught() -> None:
    """A document shared while the companies differ would still be a leak."""
    dev_side = record("DEV-0004", code="2412", name="中華電信", documents=("2330-FY2023-AR",))
    problems = leakage_problems({"dev": [dev_side], "locked": [LOCKED]})
    assert any("share documents: ['2330-FY2023-AR']" in problem for problem in problems)


# --------------------------------------------------------- the chart challenger


def test_the_challenger_may_not_use_locked_companies() -> None:
    """Protocol 2.3 selects a model with the challenger.

    Selecting it on locked crops is tuning on locked data even though no threshold was
    touched, so the challenger is pinned to the dev side.
    """
    intruder = record("CHAL-0001")
    problems = leakage_problems({"challenger": [intruder], "locked": [LOCKED]})
    assert any("not on the challenger side" in problem for problem in problems)


def test_a_dev_side_challenger_is_fine() -> None:
    challenger = record(
        "CHAL-0001",
        question="圖中乙烯產量的長條高度對應多少公噸？",
        code="1301",
        name="台塑",
        documents=("1301-FY2023-AR",),
    )
    assert leakage_problems({"challenger": [challenger], "locked": [LOCKED]}) == []


def test_probes_belong_to_the_locked_side() -> None:
    """Probes are graded in the locked run, so a dev company there would be misplaced."""
    intruder = record("PROBE-0001", code="2412", name="中華電信", documents=("2412-FY2023-AR",))
    problems = leakage_problems({"probe": [intruder], "dev": [DEV]})
    assert any("not on the probe side" in problem for problem in problems)


# ------------------------------------------------------------- reworded questions


def test_an_identical_question_on_both_sides_is_caught() -> None:
    dev_side = record(
        "DEV-0005",
        question="營業毛利率是多少？",
        code="2412",
        name="中華電信",
        documents=("2412-FY2023-AR",),
    )
    locked_side = record("LOCK-0003", question="營業毛利率是多少？")
    problems = leakage_problems({"dev": [dev_side], "locked": [locked_side]})
    assert any("ask the same question" in problem for problem in problems)


def test_a_lightly_reworded_question_is_caught() -> None:
    """The id and the wording both differ, so only similarity catches this."""
    dev_side = record(
        "DEV-0006",
        question="請問本公司民國一一二年度的營業毛利率是多少？",
        code="2412",
        name="中華電信",
        documents=("2412-FY2023-AR",),
    )
    locked_side = record("LOCK-0004", question="請問本公司民國一一二年度的營業毛利率為多少？")
    problems = leakage_problems({"dev": [dev_side], "locked": [locked_side]})
    assert any("ask the same question" in problem for problem in problems)


def test_two_genuinely_different_questions_are_not_flagged() -> None:
    dev_side = record(
        "DEV-0007",
        question="董事會的組成與獨立董事人數為何？",
        code="2412",
        name="中華電信",
        documents=("2412-FY2023-AR",),
    )
    locked_side = record("LOCK-0005", question="先進封裝的產能擴充計畫如何說明？")
    assert leakage_problems({"dev": [dev_side], "locked": [locked_side]}) == []


def test_similarity_within_one_side_is_not_leakage() -> None:
    """Two similar locked questions are a set-quality issue, not a leak across the split."""
    first = record("LOCK-0006", question="營業毛利率是多少？")
    second = record("LOCK-0007", question="營業毛利率是多少？")
    assert leakage_problems({"locked": [first, second]}) == []


# -------------------------------------------------------------------- similarity


def test_bigrams_ignore_whitespace() -> None:
    assert bigrams("營業 毛利") == bigrams("營業毛利")


def test_a_single_character_question_still_yields_a_gram() -> None:
    assert bigrams("甲") == frozenset({"甲"})


def test_an_empty_question_has_no_grams() -> None:
    assert bigrams("   ") == frozenset()


def test_identical_text_scores_one() -> None:
    assert similarity("營業毛利率", "營業毛利率") == 1.0


def test_disjoint_text_scores_zero() -> None:
    assert similarity("董事會組成", "先進封裝") == 0.0


def test_two_empty_questions_are_treated_as_identical() -> None:
    """Both carry no information, so the pair is degenerate rather than 'different'."""
    assert similarity("", "  ") == 1.0
    assert similarity("", "營業毛利") == 0.0


def test_the_threshold_is_high_enough_to_ignore_a_shared_topic() -> None:
    """Two questions about revenue are not the same question."""
    score = similarity("本年度營業收入是多少？", "本年度營業成本的組成為何？")
    assert score < NEAR_DUPLICATE_THRESHOLD


def test_near_duplicates_reports_the_pair_and_its_score() -> None:
    left = [
        record(
            "DEV-0008",
            question="營業毛利率是多少？",
            code="2412",
            name="中華電信",
            documents=("2412-FY2023-AR",),
        )
    ]
    right = [record("LOCK-0008", question="營業毛利率是多少？")]
    assert near_duplicates(left, right) == [("DEV-0008", "LOCK-0008", 1.0)]


def test_a_lowered_threshold_finds_more_pairs() -> None:
    """The threshold is a parameter so its effect is measurable, not assumed."""
    left = [
        record(
            "DEV-0009",
            question="本年度營業收入是多少？",
            code="2412",
            name="中華電信",
            documents=("2412-FY2023-AR",),
        )
    ]
    right = [record("LOCK-0009", question="本年度營業成本是多少？")]
    assert near_duplicates(left, right) == []
    assert near_duplicates(left, right, threshold=0.5)


# ------------------------------------------------------------------- annotator


def test_a_non_human_annotator_is_reported() -> None:
    """Reachable only by constructing a record outside ``parse_record``.

    The type says ``Literal["human"]``, but the file on disk is not the type, and this
    is the check the gate is judged on.
    """
    forged = record("LOCK-0010")
    object.__setattr__(forged, "annotator", "model")
    problems = leakage_problems({"locked": [forged]})
    assert any("claims annotator='model'" in problem for problem in problems)


@pytest.mark.parametrize("gold_set", ["dev", "locked", "probe", "challenger"])
def test_every_set_is_checked_for_its_annotator(gold_set: str) -> None:
    code, name, doc = (
        ("2412", "中華電信", "2412-FY2023-AR")
        if gold_set in {"dev", "challenger"}
        else ("2330", "台積電", "2330-FY2023-AR")
    )
    forged = record("X-0001", code=code, name=name, documents=(doc,))
    object.__setattr__(forged, "annotator", "auto")
    problems = leakage_problems({gold_set: [forged]})  # type: ignore[dict-item]
    assert any("claims annotator='auto'" in problem for problem in problems)
