"""Protocol 3.1 normalization and 3.3 answer scoring.

Deterministic scoring is a protocol requirement, not a preference, so these tests are the
specification: each of §3.1's seven rules has a case, and the three that are easy to get
backwards -- percent, parenthesised negatives, ROC years -- have several.
"""

from __future__ import annotations

import pytest

from twfi.eval.answers import (
    exact_match,
    is_refusal,
    normalise_text,
    numeric_match,
    period_match,
    refusal_rates,
    roc_to_common_era,
    score_answer,
    token_f1,
    unit_match,
)
from twfi.eval.gold import GoldRecord, Tolerance


def record(**overrides: object) -> GoldRecord:
    base: dict[str, object] = {
        "question_id": "DEV-0001",
        "question_type": "table_cell",
        "question": "台塑民國112年度的資產總計是多少？",
        "answer": "530,738,356",
        "company": {"name": "台塑", "code": "1301"},
        "period": "FY2023",
        "source_document": ("1301-FY2023-AR",),
        "unit": "千元",
        "answer_provenance": "human_read_pdf",
        "annotator": "human",
        "question_author": "human",
        "required_evidence": (),
        "annotated_at": "2026-08-01",
    }
    base.update(overrides)
    return GoldRecord(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------- 3.1 normalization


def test_fullwidth_folds_to_halfwidth() -> None:
    """Rule 1."""
    assert normalise_text("５３０") == normalise_text("530")


def test_thousands_separators_and_whitespace_are_removed() -> None:
    """Rule 1."""
    assert normalise_text("530, 738 ,356") == "530738356"


def test_currency_spellings_fold_together() -> None:
    """Rule 5."""
    assert normalise_text("新台幣100元") == normalise_text("NT$100元") == normalise_text("TWD100元")


@pytest.mark.parametrize(
    ("roc", "common"),
    [("112年", "2023年"), ("民國112年", "2023年"), ("113年度", "2024年度")],
)
def test_roc_years_convert(roc: str, common: str) -> None:
    """Rule 6."""
    assert roc_to_common_era(roc) == common


def test_a_four_digit_year_is_left_alone() -> None:
    """Adding 1911 to a Common Era year would invent one."""
    assert roc_to_common_era("2023年") == "2023年"


# ------------------------------------------------------------- exact match


def test_a_figure_matches_across_separator_spelling() -> None:
    assert exact_match("530738356", record())
    assert exact_match("530,738,356", record())


def test_an_acceptable_variant_matches() -> None:
    entry = record(acceptable_variants=("530738356千元",))
    assert exact_match("530,738,356 千元", entry)


def test_a_different_figure_does_not_match() -> None:
    assert not exact_match("530,738,357", record())


# --------------------------------------------------------------- token F1


def test_identical_text_scores_one() -> None:
    assert token_f1("資產總計增加", "資產總計增加") == pytest.approx(1.0)


def test_unrelated_text_scores_zero() -> None:
    assert token_f1("營業收入", "股利政策") == 0.0


def test_partial_overlap_scores_between() -> None:
    score = token_f1("資產總計為五億元", "資產總計")
    assert 0.0 < score < 1.0


def test_repetition_does_not_score_as_a_single_mention() -> None:
    """Multiset intersection: a set-based F1 cannot tell these apart."""
    once = token_f1("資產總計", "資產總計")
    repeated = token_f1("資產總計資產總計資產總計", "資產總計")
    assert repeated < once


# ------------------------------------------------------- numeric comparison


def test_a_figure_within_relative_tolerance_passes() -> None:
    """Rule 7: numbers compare as numbers."""
    entry = record(tolerance=Tolerance(type="relative", value=0.005))
    assert numeric_match("530,800,000", entry) is True


def test_a_figure_outside_relative_tolerance_fails() -> None:
    entry = record(tolerance=Tolerance(type="relative", value=0.005))
    assert numeric_match("600,000,000", entry) is False


def test_an_absolute_tolerance_is_in_the_answers_own_unit() -> None:
    entry = record(answer="39.11", unit="%", tolerance=Tolerance(type="absolute", value=0.1))
    assert numeric_match("39.15", entry) is True
    assert numeric_match("39.5", entry) is False


def test_a_parenthesised_negative_is_negative() -> None:
    """Rule 3. Reading it as positive flips the sign of a real figure."""
    entry = record(answer="-19466030")
    assert numeric_match("(19,466,030)", entry) is True


def test_a_cjk_scale_word_is_expanded() -> None:
    """Rule 2."""
    entry = record(answer="120000000")
    assert numeric_match("1.2億", entry) is True


def test_an_answer_stating_no_figure_fails_a_numeric_question() -> None:
    assert numeric_match("查無資料", record()) is False


def test_a_zero_gold_admits_only_zero() -> None:
    """A relative window around zero has zero width; widening it invents a tolerance."""
    entry = record(answer="0", tolerance=Tolerance(type="relative", value=0.005))
    assert numeric_match("0", entry) is True
    assert numeric_match("1", entry) is False


# ---------------------------------------------------------- unit and period


def test_unit_is_judged_separately_from_the_figure() -> None:
    """Protocol 3.3: right digits under the wrong unit is not right."""
    assert unit_match("千元", record()) is True
    assert unit_match("百萬元", record()) is False


def test_an_unstated_gold_unit_makes_the_metric_inapplicable() -> None:
    assert unit_match("千元", record(unit=None)) is None


def test_period_treats_roc_and_common_era_as_one() -> None:
    entry = record(period="FY2023")
    assert period_match("FY2023", entry) is True
    assert period_match("FY2024", entry) is False


def test_an_answer_stating_no_period_is_wrong_rather_than_exempt() -> None:
    assert period_match(None, record()) is False


# ------------------------------------------------------------------ refusal


@pytest.mark.parametrize(
    "text",
    ["文件中沒有這項資訊", "無法從提供的片段回答", "查無此數據", "The document does not state it"],
)
def test_a_refusal_is_recognised(text: str) -> None:
    assert is_refusal(text)


def test_an_answer_is_not_a_refusal() -> None:
    assert not is_refusal("530,738,356 千元")


def test_refusing_an_unanswerable_question_is_correct() -> None:
    entry = record(question_type="unanswerable", answerable=False, answer=None)
    score = score_answer("文件中沒有碳排放強度", entry)
    assert score.should_refuse and score.refused
    assert score.correct


def test_answering_an_unanswerable_question_is_wrong() -> None:
    entry = record(question_type="unanswerable", answerable=False, answer=None)
    assert not score_answer("12.5", entry).correct


def test_refusing_an_answerable_question_is_wrong() -> None:
    assert not score_answer("文件中沒有這項資訊", record()).correct


def test_refusal_precision_and_recall_are_reported_together() -> None:
    """Refusing everything has perfect recall; never refusing has perfect accuracy elsewhere."""
    answerable = record()
    unanswerable = record(question_type="unanswerable", answerable=False, answer=None)
    scores = [
        score_answer("530,738,356", answerable),
        score_answer("文件中沒有", unanswerable),
        score_answer("12", unanswerable),
    ]
    rates = refusal_rates(scores)
    assert rates["should_refuse"] == 2
    assert rates["refused"] == 1
    assert rates["precision"] == 1.0
    assert rates["recall"] == 0.5


def test_a_trailing_unit_is_not_read_as_a_multiplier() -> None:
    """Protocol 3.1 rule 2's parenthetical: the unit field is judged separately.

    "530,738,356 千元" against a gold of 530,738,356 in 千元 is the same figure. Reading 千元 as
    a x1000 scale made a correct answer wrong by a factor of a thousand.
    """
    entry = record(unit="千元", tolerance=Tolerance(type="relative", value=0.005))
    assert numeric_match("530,738,356 千元", entry) is True


def test_a_scale_word_still_expands_when_it_is_not_the_unit() -> None:
    entry = record(answer="120000000", unit="元")
    assert numeric_match("1.2億", entry) is True


# ----------------------------------------------------------- the primary metric


def test_a_numeric_question_is_judged_on_its_number_not_its_string() -> None:
    """Exact string match is too brittle to be primary when the unit may be appended."""
    entry = record(tolerance=Tolerance(type="relative", value=0.005))
    score = score_answer("資產總計為 530,738,356 千元", entry)
    assert not score.exact, "the sentence is not string-equal to the gold figure"
    assert score.correct, "but the figure it states is right"


def test_the_verdicts_stay_separate_in_the_payload() -> None:
    payload = score_answer("530,738,356", record()).to_json()
    assert set(payload) == {
        "exact_match",
        "token_f1",
        "numeric_ok",
        "unit_ok",
        "period_ok",
        "refused",
        "should_refuse",
        "correct",
    }
