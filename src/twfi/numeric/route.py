"""The F4 numeric route: answer a figure question from the store, deterministically.

RQ2 asks whether official structured values through deterministic SQL beat putting the numbers
into an embedding and letting a model read them back. This is the first half of that comparison
-- the SQL side -- and the ladder's F4 rung.

**The question is parsed, never the gold record.** A gold record carries
``structured_source_key`` naming the exact row, and using it here would be answering the question
with the answer key: the route would look perfect and measure nothing. So company, period and
account are recovered from the question text alone, and a question this cannot parse is refused
rather than guessed.

**Templated SQL only.** Protocol 2.4 forbids an LLM writing SQL, and nothing here composes a
query from model output. The queries come from :mod:`twfi.numeric.sql_tools`, whose ``TEMPLATES``
tuple is the complete set the route may use -- anything a question needs beyond those five is a
template miss and is refused rather than improvised.

**Every derived figure carries its formula and operands.** Protocol 2.4 again, and it is what
separates this route from the model doing arithmetic in its head: 「34.55」 on its own is a
number to be trusted, while 「183,378,211 / 530,738,356 x 100」 is a claim anyone can check.

What this deliberately does not do is widen coverage by loading more of the page. The store holds
the cells gold names, which -- as ``twfi.numeric.historical`` says in its own docstring -- means
"the numeric route had the figure it needed" must never be reported as a coverage finding. It was
arranged. What F4 *can* honestly show is whether, given the figure, the deterministic path
answers more reliably than the generative one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from twfi.errors import NumericRouteError, UnitMismatchError
from twfi.numeric.calculator import Computation
from twfi.numeric.sql_tools import LookupResult, account_ratio, lookup, period_growth
from twfi.numeric.store import NumericStore
from twfi.protocol import COMPANIES

__all__ = ["NumericAnswer", "NumericQuestion", "answer_numerically", "parse_question"]

#: Company name to code, from the protocol's own list so the two cannot drift.
_COMPANY_BY_NAME: dict[str, str] = {company.name: company.code for company in COMPANIES}

#: Accounts the route can look up, and the spellings a question uses for them. Two issuers write
#: the same line differently (資產總額 / 資產總計), and a question uses whichever its filing does.
_ACCOUNTS: dict[str, tuple[str, ...]] = {
    "資產總計": ("資產總計", "資產總額", "總資產"),
    "負債總計": ("負債總計", "負債總額", "總負債"),
    "權益總計": ("權益總計", "權益總額", "股東權益總計", "淨值"),
    "保留盈餘": ("保留盈餘",),
    "營業收入": ("營業收入", "營收"),
    "營業成本": ("營業成本",),
    "本期淨利": ("本期淨利", "稅後淨利", "稅後純益"),
    "流動資產": ("流動資產",),
    "流動負債": ("流動負債",),
    "非流動資產": ("非流動資產",),
    "非流動負債": ("非流動負債",),
}

#: 「民國112年度」 / 「112年12月31日」 / 「FY2023」.
_ROC_PERIOD = re.compile(r"(?:民國)?(?P<year>\d{2,3})\s*年")
_FY_PERIOD = re.compile(r"FY(?P<year>\d{4})", re.IGNORECASE)

#: Phrasings that ask for one figure as a share of another, and for a change between periods.
_RATIO = re.compile(r"佔|占|比率|比重|占比")
_GROWTH = re.compile(r"成長|增減|變動比例|年增|較.*(?:增加|減少|變動)")

#: A question asking for more than one figure. 「分別是多少」 names two periods; a second
#: 「是多少」 names a second quantity.
#:
#: The ``lookup`` template returns one figure, and answering a two-figure question with one of
#: them is the worst shape of failure available here: it is precise, it is correctly cited, and
#: it is only half the answer. Measured on dev -- 「台塑民國112年度的資產總計是多少，同年度的
#: 現金流量比率是多少？」 got 530,738,356, which is right about the part it addressed and wrong
#: as an answer. Refusing hands the question to the generative path, which can at least attempt
#: both halves.
_MULTI_FIGURE = re.compile(r"分別|(?:是多少.*){2,}")


@dataclass(frozen=True, slots=True)
class NumericQuestion:
    """What a question asks for, recovered from its text alone."""

    company_code: str
    period: str
    accounts: tuple[str, ...]
    kind: str  # "lookup" | "ratio" | "growth"

    @property
    def account(self) -> str:
        return self.accounts[0]


@dataclass(frozen=True, slots=True)
class NumericAnswer:
    """A figure the store can support, or a refusal that says why."""

    value: Decimal | None
    unit: str | None
    period: str | None
    formula: str = ""
    operands: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    refusal: str = ""

    @property
    def ok(self) -> bool:
        return self.value is not None

    def as_text(self) -> str:
        """The answer as the ladder's contract wants it, or the refusal."""
        if self.value is None:
            return "無法回答"
        text = f"{self.value:,.2f}".rstrip("0").rstrip(".")
        return text

    def to_json(self) -> dict[str, object]:
        return {
            "value": str(self.value) if self.value is not None else None,
            "unit": self.unit,
            "period": self.period,
            "formula": self.formula,
            "operands": list(self.operands),
            "source_refs": list(self.source_refs),
            "refusal": self.refusal,
        }


def parse_question(question: str) -> NumericQuestion | None:
    """Recover company, period and accounts from the question text, or ``None``.

    ``None`` rather than a guess. A numeric route that guesses which account was meant produces a
    figure that is precise, cited, and about the wrong line -- the worst available failure, since
    everything about it looks trustworthy.
    """
    code = next((c for name, c in _COMPANY_BY_NAME.items() if name in question), None)
    if code is None:
        return None

    fy = _FY_PERIOD.search(question)
    if fy:
        period = f"FY{fy.group('year')}"
    else:
        roc = _ROC_PERIOD.search(question)
        if roc is None:
            return None
        period = f"FY{int(roc.group('year')) + 1911}"

    # Longest spelling first, so 非流動資產 is not matched as 流動資產.
    found: list[tuple[int, str]] = []
    for canonical, spellings in _ACCOUNTS.items():
        for spelling in spellings:
            position = question.find(spelling)
            if position >= 0:
                found.append((position, canonical))
                break
    if not found:
        return None
    # Deduplicate, keeping the order they appear in the question: 「負債總計佔資產總計」 asks for
    # the first as a share of the second, and reversing them inverts the ratio.
    ordered = tuple(dict.fromkeys(name for _, name in sorted(found)))

    if _RATIO.search(question) and len(ordered) >= 2:
        kind = "ratio"
    elif _MULTI_FIGURE.search(question):
        # More figures wanted than one lookup returns. Refused rather than half-answered.
        return None
    elif _GROWTH.search(question):
        kind = "growth"
    else:
        kind = "lookup"
    return NumericQuestion(company_code=code, period=period, accounts=ordered, kind=kind)


def _figure(store: NumericStore, code: str, period: str, account: str) -> LookupResult | None:
    """One figure through the templated lookup, or ``None`` if the store has not got it."""
    try:
        return lookup(store, code, account, period)
    except (NumericRouteError, UnitMismatchError):
        return None


def answer_numerically(question: str, store: NumericStore) -> NumericAnswer:
    """Answer a figure question from the store, or refuse with a reason.

    Refusals are specific on purpose. "cannot parse the question" and "the store does not hold
    that figure" are different findings about the route, and a single "no" would merge a parsing
    limit with a coverage limit.
    """
    parsed = parse_question(question)
    if parsed is None:
        return NumericAnswer(
            value=None,
            unit=None,
            period=None,
            refusal="the question names no company, period and account this route recognises",
        )

    try:
        if parsed.kind == "ratio" and len(parsed.accounts) >= 2:
            computed = account_ratio(
                store, parsed.company_code, parsed.accounts[0], parsed.accounts[1], parsed.period
            )
            return _from_computation(computed, parsed.period)
        if parsed.kind == "growth":
            earlier = f"FY{int(parsed.period[2:]) - 1}"
            computed = period_growth(
                store, parsed.company_code, parsed.account, parsed.period, earlier
            )
            return _from_computation(computed, parsed.period)
    except (NumericRouteError, UnitMismatchError) as exc:
        return NumericAnswer(
            value=None, unit=None, period=parsed.period, refusal=f"{type(exc).__name__}: {exc}"
        )

    found = _figure(store, parsed.company_code, parsed.period, parsed.account)
    if found is None:
        return NumericAnswer(
            value=None,
            unit=None,
            period=parsed.period,
            refusal=(
                f"the store holds no {parsed.account} for {parsed.company_code} {parsed.period}"
            ),
        )
    item = found.item
    return NumericAnswer(
        value=item.value,
        unit=item.unit,
        period=parsed.period,
        formula=parsed.account,
        operands=(f"{parsed.account}={item.value}",),
        source_refs=found.citations(),
    )


def _from_computation(computation: Computation, period: str) -> NumericAnswer:
    return NumericAnswer(
        value=computation.value,
        unit=computation.unit,
        period=period,
        formula=computation.formula,
        operands=tuple(operand.render() for operand in computation.operands),
        source_refs=computation.citations(),
    )
