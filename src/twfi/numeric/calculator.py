"""Arithmetic over stored figures, with the formula and every operand shown.

Protocol 2.4 requires a calculated answer to state its formula and operands. That is
not presentation: it is what lets a reader check the answer without trusting the
system, and what lets error analysis tell "retrieved the wrong row" apart from
"computed the wrong thing".

The comparability checks are the substance here. Two figures may only be combined
when they agree on unit, currency, and statement basis, and when neither came from a
source that qualified its own unit. Any of those failing raises
:class:`~twfi.errors.UnitMismatchError` -- because the alternative, quietly computing
anyway, produces a number that is plausible, precise, and wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from twfi.errors import UnitMismatchError
from twfi.numeric.amounts import same_scale
from twfi.numeric.store import LineItem

__all__ = ["Operand", "Computation", "require_comparable", "difference", "growth_rate", "ratio"]

_PERCENT = "%"


def _format(value: Decimal) -> str:
    """Render a figure the way a filing does, with thousands separators."""
    quantised = value.quantize(Decimal("0.01")) if value % 1 else value.quantize(Decimal("1"))
    return f"{quantised:,}"


@dataclass(frozen=True, slots=True)
class Operand:
    """One input to a calculation, and where it came from."""

    label: str
    item: LineItem

    @property
    def value(self) -> Decimal:
        if self.item.value is None:
            raise UnitMismatchError(f"{self.label}: the source recorded no value")
        return self.item.value

    def render(self) -> str:
        unit = self.item.unit or "單位未載明"
        return f"{self.label}({self.item.period})={_format(self.value)} {unit}"

    def citation(self) -> str:
        return self.item.citation()


@dataclass(frozen=True, slots=True)
class Computation:
    """A calculated answer with its derivation attached."""

    template: str
    value: Decimal
    unit: str | None
    currency: str | None
    formula: str
    operands: tuple[Operand, ...]

    def citations(self) -> tuple[str, ...]:
        return tuple(operand.citation() for operand in self.operands)

    def describe(self) -> str:
        return f"{_format(self.value)} {self.unit or ''}".strip()


def require_comparable(operands: tuple[Operand, ...], *, need_same_unit: bool = True) -> None:
    """Check that these figures may legitimately be combined.

    Raises:
        UnitMismatchError: On a missing value, a source that qualified its own unit, or
            a disagreement on unit, currency, or statement basis.
    """
    if len(operands) < 2:
        raise UnitMismatchError("a comparison needs at least two operands")

    for operand in operands:
        item = operand.item
        if item.value is None:
            raise UnitMismatchError(f"{operand.label}: the source recorded no value")
        if not item.unit_is_uniform:
            note = item.unit_note or "the source qualified its own unit"
            raise UnitMismatchError(
                f"{operand.label}: unit is not uniform for this figure ({note}); "
                "resolve the exception before computing with it"
            )
        if item.unit is None:
            raise UnitMismatchError(
                f"{operand.label}: no unit was stated, so this figure cannot be combined"
            )

    first = operands[0].item
    for operand in operands[1:]:
        item = operand.item
        if item.basis != first.basis:
            raise UnitMismatchError(
                f"cannot combine {first.basis} with {item.basis}: "
                "consolidated and parent-only figures are different quantities"
            )
        if item.currency != first.currency:
            raise UnitMismatchError(
                f"currency mismatch: {first.currency or 'unstated'} vs "
                f"{item.currency or 'unstated'}"
            )
        if need_same_unit and not same_scale(first.unit, item.unit):
            raise UnitMismatchError(
                f"unit mismatch: {first.unit} vs {item.unit}; "
                "one source reports in a different scale"
            )


def difference(later: Operand, earlier: Operand) -> Computation:
    """``later - earlier``, for cross-period comparison.

    Raises:
        UnitMismatchError: If the two figures are not comparable.
    """
    require_comparable((later, earlier))
    value = later.value - earlier.value
    formula = (
        f"{later.label}({later.item.period}) − {earlier.label}({earlier.item.period}) = "
        f"{_format(later.value)} − {_format(earlier.value)} = {_format(value)}"
    )
    return Computation(
        template="difference",
        value=value,
        unit=later.item.unit,
        currency=later.item.currency,
        formula=formula,
        operands=(later, earlier),
    )


def growth_rate(later: Operand, earlier: Operand) -> Computation:
    """Percentage change from ``earlier`` to ``later``.

    Raises:
        UnitMismatchError: If the figures are not comparable, or the base is zero --
            growth from nothing is undefined, not infinite.
    """
    require_comparable((later, earlier))
    if earlier.value == 0:
        raise UnitMismatchError(
            f"{earlier.label}({earlier.item.period}) is zero; a growth rate from zero is undefined"
        )
    change = (later.value - earlier.value) / earlier.value * Decimal(100)
    value = change.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    formula = (
        f"({later.label}({later.item.period}) − {earlier.label}({earlier.item.period})) ÷ "
        f"{earlier.label}({earlier.item.period}) × 100 = "
        f"({_format(later.value)} − {_format(earlier.value)}) ÷ "
        f"{_format(earlier.value)} × 100 = {value}%"
    )
    return Computation(
        template="growth_rate",
        value=value,
        unit=_PERCENT,
        currency=None,
        formula=formula,
        operands=(later, earlier),
    )


def ratio(numerator: Operand, denominator: Operand, *, as_percent: bool = True) -> Computation:
    """``numerator / denominator``, as a percentage by default.

    The units must match so they cancel; a ratio of 千元 to 百萬元 is not a ratio.

    Raises:
        UnitMismatchError: If the figures are not comparable or the denominator is zero.
    """
    require_comparable((numerator, denominator))
    if denominator.value == 0:
        raise UnitMismatchError(f"{denominator.label} is zero; the ratio is undefined")

    raw = numerator.value / denominator.value
    scaled = raw * Decimal(100) if as_percent else raw
    value = scaled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    tail = " × 100" if as_percent else ""
    formula = (
        f"{numerator.label} ÷ {denominator.label}{tail} = "
        f"{_format(numerator.value)} ÷ {_format(denominator.value)}{tail} = "
        f"{value}{_PERCENT if as_percent else ''}"
    )
    return Computation(
        template="ratio",
        value=value,
        unit=_PERCENT if as_percent else "倍",
        currency=None,
        formula=formula,
        operands=(numerator, denominator),
    )
