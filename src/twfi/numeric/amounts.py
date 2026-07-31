"""Reading and rescaling the amounts printed in Taiwanese filings.

Protocol 3.1 fixes the normalisation rules, and the same rules have to hold in two
places: here, when a figure is loaded into DuckDB, and later when an answer is scored.
Implementing them twice would let the loader and the grader disagree, so they are
implemented once.

The rules that actually bite in this corpus:

* **Parenthesised negatives.** ``(12,345)`` is minus twelve thousand. Reading it as
  positive is the single most damaging silent error available, because the magnitude
  is right and only the sign is wrong.
* **Two spellings of the same scale.** ``仟元`` and ``千元`` are one unit.
* **Scales that differ by a thousand between sources.** ``t187ap17_L`` reports
  營業收入 in 百萬元 while ``t187ap06_L_ci`` reports it in 千元.
* **Full-width digits**, which appear in narrative text.

Everything is :class:`~decimal.Decimal`. Financial figures compared at a 0.5%
tolerance do not need binary floating point's rounding surprises on top.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

__all__ = [
    "UNIT_SCALES",
    "canonical_unit",
    "parse_amount",
    "to_base_units",
    "convert",
    "same_scale",
]

#: How many 元 one of each unit is worth. A unit outside this table is not rescalable
#: and any attempt to convert it raises rather than guessing.
UNIT_SCALES: dict[str, Decimal] = {
    "元": Decimal(1),
    "千元": Decimal(1_000),
    "萬元": Decimal(10_000),
    "百萬元": Decimal(1_000_000),
    "億元": Decimal(100_000_000),
    "十億元": Decimal(1_000_000_000),
}

#: Spellings that mean the same unit. Treating 仟元 and 千元 as different would report
#: unit errors that are not errors.
_UNIT_ALIASES = {
    "仟元": "千元",
    "仟股": "千股",
    "百萬": "百萬元",
    "億": "億元",
    "萬": "萬元",
    "千": "千元",
}

_PARENTHESISED = re.compile(r"^\(\s*(.+?)\s*\)$|^（\s*(.+?)\s*）$")
_NOT_NUMERIC = re.compile(r"[^0-9.\-+eE]")
_PLACEHOLDERS = frozenset({"", "-", "–", "—", "－", "n/a", "na", "nil", "不適用", "無"})


def canonical_unit(unit: str | None) -> str | None:
    """Normalise a unit spelling, or return ``None`` for an unstated unit."""
    if unit is None:
        return None
    stripped = unicodedata.normalize("NFKC", unit).strip()
    if not stripped:
        return None
    return _UNIT_ALIASES.get(stripped, stripped)


def parse_amount(text: str | None) -> Decimal | None:
    """Parse a printed amount, or ``None`` if the cell holds no figure.

    Handles parenthesised negatives, thousands separators, percent signs, full-width
    digits, and the dash placeholders filings use for "nothing here". Returns ``None``
    rather than zero for those: a missing figure and a zero figure are different
    answers, and conflating them would turn an unanswerable question into a wrong one.
    """
    if text is None:
        return None

    cleaned = unicodedata.normalize("NFKC", str(text)).strip()
    if cleaned.lower() in _PLACEHOLDERS:
        return None

    negative = False
    match = _PARENTHESISED.match(cleaned)
    if match:
        negative = True
        cleaned = match.group(1) or match.group(2) or ""

    cleaned = cleaned.replace("%", "").replace("　", "").strip()
    cleaned = _NOT_NUMERIC.sub("", cleaned)
    if cleaned in {"", "-", "+", ".", "-.", "+."}:
        return None

    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    return -value if negative else value


def to_base_units(value: Decimal, unit: str | None) -> Decimal:
    """Convert an amount to 元.

    Raises:
        KeyError: If the unit is unknown or unstated. Guessing a scale is how a figure
            ends up wrong by a factor of a thousand while looking plausible.
    """
    canonical = canonical_unit(unit)
    if canonical is None:
        raise KeyError("cannot rescale an amount whose unit was never stated")
    if canonical not in UNIT_SCALES:
        raise KeyError(f"unit {canonical!r} has no defined scale")
    return value * UNIT_SCALES[canonical]


def convert(value: Decimal, from_unit: str | None, to_unit: str | None) -> Decimal:
    """Rescale an amount between two monetary units.

    Raises:
        KeyError: If either unit is unknown or unstated.
    """
    target = canonical_unit(to_unit)
    if target is None or target not in UNIT_SCALES:
        raise KeyError(f"unit {to_unit!r} has no defined scale")
    return to_base_units(value, from_unit) / UNIT_SCALES[target]


def same_scale(left: str | None, right: str | None) -> bool:
    """Whether two unit spellings denote the same scale.

    Two unstated units are *not* the same scale: nothing is known about either, so
    comparing figures across them is not justified.
    """
    first = canonical_unit(left)
    second = canonical_unit(right)
    if first is None or second is None:
        return False
    return first == second
