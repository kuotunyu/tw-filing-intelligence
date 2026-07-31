"""Reading an amount wrong is the cheapest way to be confidently incorrect."""

from __future__ import annotations

from decimal import Decimal

import pytest

from twfi.numeric.amounts import (
    UNIT_SCALES,
    canonical_unit,
    convert,
    parse_amount,
    same_scale,
    to_base_units,
)

# -------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1234", Decimal(1234)),
        ("1,234", Decimal(1234)),
        ("2,894,308", Decimal(2894308)),
        ("1134103440.00", Decimal("1134103440.00")),
        ("22.08", Decimal("22.08")),
        ("-500", Decimal(-500)),
        ("０１２３", Decimal(123)),
        ("45.6%", Decimal("45.6")),
    ],
)
def test_amounts_parse(text: str, expected: Decimal) -> None:
    assert parse_amount(text) == expected


@pytest.mark.parametrize("text", ["(12,345)", "（12,345）", "( 12,345 )"])
def test_parenthesised_negatives(text: str) -> None:
    """The accounting convention. Reading it as positive gets the sign wrong silently."""
    assert parse_amount(text) == Decimal(-12345)


def test_a_parenthesised_negative_is_not_the_same_as_a_positive() -> None:
    assert parse_amount("(12,345)") != parse_amount("12,345")


@pytest.mark.parametrize("text", ["", "  ", "-", "–", "—", "n/a", "NA", "不適用", "無", None])
def test_placeholders_are_absent_not_zero(text: str | None) -> None:
    """A missing figure and a zero figure are different answers."""
    assert parse_amount(text) is None


def test_zero_is_a_real_value() -> None:
    assert parse_amount("0") == Decimal(0)


def test_unparseable_text_is_none_not_an_exception() -> None:
    """A corrupt cell must not abort a load of a thousand rows."""
    assert parse_amount("計算中") is None


# ---------------------------------------------------------------------- units


@pytest.mark.parametrize(
    ("raw", "expected"), [("仟元", "千元"), ("千元", "千元"), ("仟股", "千股")]
)
def test_unit_spellings_are_canonicalised(raw: str, expected: str) -> None:
    """仟元 and 千元 are one unit; splitting them would invent unit errors."""
    assert canonical_unit(raw) == expected


def test_an_unstated_unit_stays_unstated() -> None:
    assert canonical_unit(None) is None
    assert canonical_unit("   ") is None


def test_scales_are_powers_of_ten_apart() -> None:
    assert UNIT_SCALES["千元"] == Decimal(1_000)
    assert UNIT_SCALES["百萬元"] == Decimal(1_000_000)
    assert UNIT_SCALES["億元"] == Decimal(100_000_000)


def test_to_base_units() -> None:
    assert to_base_units(Decimal(2894308), "千元") == Decimal("2894308000")


def test_rescaling_between_the_two_scales_this_corpus_uses() -> None:
    """t187ap17_L reports 營業收入 in 百萬元; t187ap06_L_ci reports it in 千元."""
    assert convert(Decimal(1134103), "百萬元", "千元") == Decimal("1134103000")


def test_rescaling_refuses_an_unstated_unit() -> None:
    """Guessing a scale is how a figure ends up wrong by a factor of a thousand."""
    with pytest.raises(KeyError, match="never stated"):
        to_base_units(Decimal(1), None)


def test_rescaling_refuses_an_unknown_unit() -> None:
    with pytest.raises(KeyError, match="no defined scale"):
        to_base_units(Decimal(1), "打")
    with pytest.raises(KeyError, match="no defined scale"):
        convert(Decimal(1), "千元", "%")


def test_same_scale_across_spellings() -> None:
    assert same_scale("仟元", "千元") is True
    assert same_scale("千元", "百萬元") is False


def test_two_unstated_units_are_not_the_same_scale() -> None:
    """Nothing is known about either, so comparing across them is not justified."""
    assert same_scale(None, None) is False
    assert same_scale("千元", None) is False
