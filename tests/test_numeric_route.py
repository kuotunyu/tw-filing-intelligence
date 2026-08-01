"""The F4 numeric route.

The property that matters most: the route parses the *question*, never the gold record. A gold
record names the exact row in ``structured_source_key``, and a route that read it would answer
every question perfectly and measure nothing. So these tests hand it question text only.

The second property: it refuses rather than half-answers. A figure that is precise, correctly
cited, and only part of what was asked is the worst failure available here, because everything
about it looks trustworthy.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from twfi.numeric.route import answer_numerically, parse_question
from twfi.numeric.store import CompanyRow, LineItem, NumericStore


@pytest.fixture()
def store() -> NumericStore:
    with NumericStore() as opened:
        opened.add_companies([CompanyRow(code="1301", name="台塑", industry_schema="general")])
        opened.add_line_items(
            [
                LineItem(
                    company_code="1301",
                    period=period,
                    statement="balance",
                    basis="consolidated",
                    industry_schema="general",
                    account=account,
                    value=Decimal(value),
                    unit="千元",
                    currency="TWD",
                    source_kind="extracted_table",
                    source_ref=f"1301-{period}|{account}",
                )
                for account, period, value in (
                    ("資產總計", "FY2023", "530738356"),
                    ("資產總計", "FY2022", "511254407"),
                    ("負債總計", "FY2023", "183378211"),
                )
            ]
        )
        yield opened


# ------------------------------------------------------------------ parsing


def test_company_period_and_account_come_from_the_question() -> None:
    parsed = parse_question("台塑民國112年度的資產總計是多少？")
    assert parsed is not None
    assert (parsed.company_code, parsed.period, parsed.account) == ("1301", "FY2023", "資產總計")


def test_a_western_year_parses_too() -> None:
    parsed = parse_question("台塑 FY2023 的資產總計是多少？")
    assert parsed is not None and parsed.period == "FY2023"


def test_an_alternative_spelling_maps_to_the_same_account() -> None:
    """Two issuers write the same line differently and a question uses its filing's wording."""
    parsed = parse_question("台塑民國112年度的資產總額是多少？")
    assert parsed is not None and parsed.account == "資產總計"


def test_a_question_naming_no_known_company_is_refused() -> None:
    assert parse_question("某公司民國112年度的資產總計是多少？") is None


def test_a_question_naming_no_period_is_refused() -> None:
    assert parse_question("台塑的資產總計是多少？") is None


def test_a_narrative_question_is_refused_rather_than_guessed() -> None:
    assert parse_question("台塑民國112年度是否有現金不足額情形？") is None


def test_a_ratio_question_keeps_the_order_it_was_asked_in() -> None:
    """Reversing numerator and denominator inverts the answer."""
    parsed = parse_question("台塑民國112年度的負債總計佔資產總計的比率是多少？")
    assert parsed is not None
    assert parsed.kind == "ratio"
    assert parsed.accounts[:2] == ("負債總計", "資產總計")


def test_a_multi_figure_question_is_refused() -> None:
    """One lookup returns one figure; answering half of a two-part question looks trustworthy."""
    assert parse_question("台塑的資產總計，民國111年度與民國112年度分別是多少？") is None
    assert parse_question("台塑民國112年度的資產總計是多少，同年度的負債總計是多少？") is None


# ------------------------------------------------------------------ answering


def test_a_lookup_returns_the_stored_figure(store: NumericStore) -> None:
    answer = answer_numerically("台塑民國112年度的資產總計是多少？", store)
    assert answer.ok
    assert answer.value == Decimal("530738356")
    assert answer.unit == "千元"


def test_a_lookup_carries_its_source(store: NumericStore) -> None:
    """A figure that cannot be traced back to a row is not evidence."""
    answer = answer_numerically("台塑民國112年度的資產總計是多少？", store)
    assert answer.source_refs and "1301-FY2023" in answer.source_refs[0]


def test_a_ratio_carries_its_formula_and_operands(store: NumericStore) -> None:
    """Protocol 2.4: a derived figure without its derivation is a number to be trusted."""
    answer = answer_numerically("台塑民國112年度的負債總計佔資產總計的比率是多少？", store)
    assert answer.ok
    assert answer.value == pytest.approx(Decimal("34.55"), abs=Decimal("0.01"))
    assert answer.formula
    assert len(answer.operands) == 2


def test_a_growth_question_uses_the_previous_period(store: NumericStore) -> None:
    answer = answer_numerically("台塑民國112年度的資產總計較前一年度成長多少？", store)
    assert answer.ok
    assert answer.value == pytest.approx(Decimal("3.81"), abs=Decimal("0.01"))


def test_a_missing_figure_refuses_with_a_reason(store: NumericStore) -> None:
    """ "cannot parse" and "not in the store" are different findings about the route."""
    answer = answer_numerically("台塑民國112年度的營業收入是多少？", store)
    assert not answer.ok
    assert "營業收入" in answer.refusal


def test_an_unparseable_question_refuses_differently(store: NumericStore) -> None:
    answer = answer_numerically("台塑的董事長是誰？", store)
    assert not answer.ok
    assert "names no company" in answer.refusal


def test_a_refusal_renders_as_the_contract_expects(store: NumericStore) -> None:
    assert answer_numerically("台塑的董事長是誰？", store).as_text() == "無法回答"


def test_a_figure_renders_without_spurious_decimals(store: NumericStore) -> None:
    assert answer_numerically("台塑民國112年度的資產總計是多少？", store).as_text() == "530,738,356"


# --------------------------------------------- a change: as a percentage or as an amount


def test_a_change_asked_as_a_proportion_is_a_percentage() -> None:
    assert (
        parse_question("台塑民國112年度的非流動負債，較前一年度的變動比例是多少？").kind == "growth"
    )


def test_a_change_asked_as_an_amount_is_a_difference() -> None:
    """「增加了多少」 wants 735,913, not 0.14 -- same shape of question, different answer."""
    parsed = parse_question("台塑的資產總計，民國112年度較民國111年度增加了多少？")
    assert parsed.kind == "delta"


def test_a_difference_returns_the_amount_and_keeps_the_unit(store: NumericStore) -> None:
    """Reading every change as a growth rate answered DEV-0015 with 0.14 from correct operands."""
    answer = answer_numerically("台塑的資產總計，民國112年度較民國111年度增加了多少？", store)
    assert answer.ok
    assert answer.value == Decimal("19483949")
    assert answer.unit == "千元"


# ------------------------------------- an account named only as a rate's denominator


def test_an_account_inside_a_per_unit_gloss_is_not_the_subject() -> None:
    """DEV-0011: 「碳排放強度（每百萬元營收的排放公噸數）」 is not a question about 營收."""
    assert parse_question("台塑民國112年度的碳排放強度（每百萬元營收的排放公噸數）是多少？") is None


def test_the_gloss_rule_holds_even_when_the_store_has_the_figure() -> None:
    """The gold-keyed store hid this by not holding 營業收入; a fuller store does hold it.

    The failure this pins is the expensive kind: a real figure, correctly cited, returned as the
    answer to a question about something else entirely.
    """
    with NumericStore() as opened:
        opened.add_companies([CompanyRow(code="1301", name="台塑", industry_schema="general")])
        opened.add_line_items(
            [
                LineItem(
                    company_code="1301",
                    period="FY2023",
                    statement="income",
                    basis="consolidated",
                    industry_schema="general",
                    account="營業收入",
                    value=Decimal("199138777"),
                    unit="千元",
                    currency="TWD",
                    source_kind="extracted_text_row",
                    source_ref="1301-FY2023-AR|p189|營業收入|112年度",
                )
            ]
        )
        answer = answer_numerically(
            "台塑民國112年度的碳排放強度（每百萬元營收的排放公噸數）是多少？", opened
        )
        assert not answer.ok
        assert answer.as_text() == "無法回答"


def test_an_account_named_outside_a_gloss_still_matches(store: NumericStore) -> None:
    """The gloss rule must not suppress a question that genuinely asks for the account."""
    answer = answer_numerically("台塑民國112年度的資產總計（每股計算）是多少？", store)
    assert answer.ok


def test_the_gloss_rule_does_not_swallow_an_unbracketed_question(store: NumericStore) -> None:
    """An earlier unbracketed pattern stripped 「每年財報的資產總計是多少？」 and refused a real
    question. Suppressing a genuine lookup is worse than missing an unbracketed gloss."""
    answer = answer_numerically("台塑民國112年度每年財報的資產總計是多少？", store)
    assert answer.ok
    assert answer.value == Decimal("530738356")
