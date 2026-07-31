"""The numeric route's whole query surface: four templates, no free-form SQL.

DECISIONS D-005 forbids letting a model write SQL. The reason is attributability, not
safety: a generated query that returns the wrong number fails in a way nobody can
reproduce or count, whereas a fixed template either covers a question or does not.
A question outside the templates raises :class:`~twfi.errors.TemplateMissError`, which
error analysis records as a capability limit rather than as a wrong answer.

So the router's job is to pick a template and fill its parameters. It cannot express
anything else, and that is the point.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from twfi.errors import TemplateMissError
from twfi.numeric.calculator import Computation, Operand, difference, growth_rate, ratio
from twfi.numeric.store import Basis, LineItem, NumericStore, SourceKind

__all__ = [
    "TemplateName",
    "TEMPLATES",
    "LookupResult",
    "lookup",
    "period_delta",
    "period_growth",
    "account_ratio",
    "cross_source_check",
    "resolve_template",
]

TemplateName = Literal["lookup", "difference", "growth_rate", "ratio", "cross_source_check"]

#: The complete set. Anything a question needs beyond these is a template miss.
TEMPLATES: tuple[TemplateName, ...] = (
    "lookup",
    "difference",
    "growth_rate",
    "ratio",
    "cross_source_check",
)


@dataclass(frozen=True, slots=True)
class LookupResult:
    """A single retrieved figure, presented like a computation for uniform handling."""

    item: LineItem

    @property
    def value(self) -> object:
        return self.item.value

    @property
    def unit(self) -> str | None:
        return self.item.unit

    def citations(self) -> tuple[str, ...]:
        return (self.item.citation(),)

    def describe(self) -> str:
        return f"{self.item.value} {self.item.unit or ''}".strip()


def lookup(
    store: NumericStore,
    company_code: str,
    account: str,
    period: str,
    *,
    basis: Basis = "consolidated",
    statement: str | None = None,
    source_kind: SourceKind | None = None,
) -> LookupResult:
    """Read one figure.

    Raises:
        NumericRouteError: If the account, period, or company is unavailable, or if
            sources disagree.
    """
    return LookupResult(
        store.require(
            company_code,
            account,
            period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        )
    )


def period_delta(
    store: NumericStore,
    company_code: str,
    account: str,
    later_period: str,
    earlier_period: str,
    *,
    basis: Basis = "consolidated",
    statement: str | None = None,
    source_kind: SourceKind | None = None,
) -> Computation:
    """Absolute change in one account between two periods.

    Raises:
        NumericRouteError: If either figure is unavailable.
        UnitMismatchError: If the two figures are not comparable.
    """
    later = Operand(
        account,
        store.require(
            company_code,
            account,
            later_period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    earlier = Operand(
        account,
        store.require(
            company_code,
            account,
            earlier_period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    return difference(later, earlier)


def period_growth(
    store: NumericStore,
    company_code: str,
    account: str,
    later_period: str,
    earlier_period: str,
    *,
    basis: Basis = "consolidated",
    statement: str | None = None,
    source_kind: SourceKind | None = None,
) -> Computation:
    """Percentage change in one account between two periods.

    Raises:
        NumericRouteError: If either figure is unavailable.
        UnitMismatchError: If the figures are not comparable or the base is zero.
    """
    later = Operand(
        account,
        store.require(
            company_code,
            account,
            later_period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    earlier = Operand(
        account,
        store.require(
            company_code,
            account,
            earlier_period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    return growth_rate(later, earlier)


def account_ratio(
    store: NumericStore,
    company_code: str,
    numerator_account: str,
    denominator_account: str,
    period: str,
    *,
    basis: Basis = "consolidated",
    as_percent: bool = True,
    statement: str | None = None,
    source_kind: SourceKind | None = None,
) -> Computation:
    """One account as a proportion of another, in the same period.

    Raises:
        NumericRouteError: If either account is unavailable for this company.
        UnitMismatchError: If the figures are not comparable or the denominator is zero.
    """
    numerator = Operand(
        numerator_account,
        store.require(
            company_code,
            numerator_account,
            period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    denominator = Operand(
        denominator_account,
        store.require(
            company_code,
            denominator_account,
            period,
            basis=basis,
            statement=statement,
            source_kind=source_kind,
        ),
    )
    return ratio(numerator, denominator, as_percent=as_percent)


@dataclass(frozen=True, slots=True)
class SourceComparison:
    """What two independent sources say about the same figure."""

    account: str
    period: str
    items: tuple[LineItem, ...]
    agree: bool
    note: str = ""

    def citations(self) -> tuple[str, ...]:
        return tuple(item.citation() for item in self.items)


def cross_source_check(
    store: NumericStore,
    company_code: str,
    account: str,
    period: str,
    *,
    basis: Basis = "consolidated",
    tolerance: float = 0.005,
) -> SourceComparison:
    """Compare what every loaded source says about one figure.

    This is the template behind the cross-document questions, and behind the conflict
    cases the protocol asks the system to refuse rather than resolve. It reports
    disagreement; it does not pick a winner.

    Raises:
        NumericRouteError: If fewer than two sources hold this figure.
    """
    items = store.find(company_code, account, period, basis=basis)
    if len(items) < 2:
        raise TemplateMissError(
            f"cross-source comparison needs two sources for {company_code} {period} "
            f"{account}; found {len(items)}"
        )

    values = [item.value for item in items if item.value is not None]
    if len(values) < 2:
        return SourceComparison(
            account=account,
            period=period,
            items=tuple(items),
            agree=False,
            note="a source is empty",
        )

    units = {item.unit for item in items}
    if len(units) > 1:
        return SourceComparison(
            account=account,
            period=period,
            items=tuple(items),
            agree=False,
            note=f"sources report different units: {sorted(str(unit) for unit in units)}",
        )

    largest = max(abs(value) for value in values)
    spread = max(values) - min(values)
    agree = largest == 0 or abs(spread / largest) <= Decimal(str(tolerance))
    return SourceComparison(
        account=account,
        period=period,
        items=tuple(items),
        agree=bool(agree),
        note="" if agree else f"values differ by {spread}",
    )


def resolve_template(name: str) -> TemplateName:
    """Validate a template name coming from the router.

    Raises:
        TemplateMissError: If the router asked for something that does not exist.
    """
    for template in TEMPLATES:
        if name == template:
            return template
    raise TemplateMissError(
        f"no numeric template named {name!r}; available: {list(TEMPLATES)}. "
        "The numeric route answers only what a template covers."
    )
