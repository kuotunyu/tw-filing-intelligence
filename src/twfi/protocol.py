"""The pre-registered protocol, as code.

``docs/FEASIBILITY_PROTOCOL.md`` is the authoritative statement of the protocol
for a human reader. This module is the same content in a form the harness can
enforce, and ``tests/test_protocol_constants.py`` asserts the two agree -- so a
number cannot be quietly changed in one place only.

Nothing here may be edited after ``scripts/freeze_protocol.py`` has run.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, get_args

__all__ = [
    "PROTOCOL_VERSION",
    "Split",
    "QuestionType",
    "Route",
    "DocType",
    "COMPANIES",
    "DEV_COMPANY_CODES",
    "LOCKED_COMPANY_CODES",
    "split_for_company",
    "DeclaredDocument",
    "DECLARED_DOCUMENTS",
    "USABLE_DOCUMENTS",
    "LOCKED_TYPE_COUNTS",
    "LOCKED_TOTAL",
    "DEV_TOTAL",
    "PROBE_COUNT",
    "CHALLENGER_ITEMS",
    "HARD_CATEGORIES",
    "POOLED_HARD_SIZE",
    "ROUTE_BY_QUESTION_TYPE",
    "FACTOR_IDS",
    "BASELINE_FACTOR",
    "CANDIDATE_FACTOR",
    "Gates",
    "GATES",
    "CHALLENGER_SWITCH_MIN_GAIN_PP",
    "consistency_problems",
]

PROTOCOL_VERSION: Final = "1.0.0-draft"

Split = Literal["dev", "locked", "both"]
DocType = Literal["annual_report", "financial_report", "xbrl"]
QuestionType = Literal[
    "narrative_fact",
    "table_cell",
    "numeric_calculation",
    "cross_period_comparison",
    "chart_value_trend",
    "cross_page",
    "cross_document",
    "unanswerable",
]
Route = Literal["narrative", "numeric", "chart", "cross_modal", "metadata", "unanswerable"]


# --------------------------------------------------------------------- companies


@dataclass(frozen=True, slots=True)
class Company:
    """A company in the study, with its industry and its immutable split."""

    code: str
    name: str
    industry: str
    split: Literal["dev", "locked"]
    fiscal_years: tuple[int, ...]


#: Protocol 1.2. Dev and locked are disjoint at the *company* level, which is the
#: strictest available separation: no paragraph, table, or figure from a locked
#: document can have been seen while tuning on dev.
COMPANIES: Final[tuple[Company, ...]] = (
    Company("2412", "中華電信", "電信", "dev", (2023,)),
    Company("1301", "台塑", "塑膠／石化", "dev", (2023,)),
    Company("2330", "台積電", "半導體", "locked", (2023, 2024)),
    Company("2317", "鴻海", "電子製造服務", "locked", (2023, 2024)),
    Company("2882", "國泰金控", "金融保險", "locked", (2024,)),
)

DEV_COMPANY_CODES: Final[frozenset[str]] = frozenset(c.code for c in COMPANIES if c.split == "dev")
LOCKED_COMPANY_CODES: Final[frozenset[str]] = frozenset(
    c.code for c in COMPANIES if c.split == "locked"
)

_COMPANY_BY_CODE: Final = MappingProxyType({c.code: c for c in COMPANIES})


def split_for_company(code: str) -> Literal["dev", "locked"]:
    """Return the pre-registered split of a company.

    Raises:
        KeyError: If the company is not part of the study. Adding one means
            amending the protocol, not calling this with a new code.
    """
    return _COMPANY_BY_CODE[code].split


# ------------------------------------------------------------------- documents


@dataclass(frozen=True, slots=True)
class DeclaredDocument:
    """One filing the study uses, and whether it can serve as evidence.

    ``usable=False`` documents stay declared on purpose. That one of seven public
    annual reports has an unreadable text layer is a feasibility finding in its own
    right, and deleting the record would delete the finding.
    """

    company_code: str
    fiscal_year: int
    doc_type: DocType
    split: Literal["dev", "locked"]
    usable: bool = True
    note: str = ""

    @property
    def token(self) -> str:
        return "AR" if self.doc_type == "annual_report" else "FS"

    @property
    def doc_id(self) -> str:
        return f"{self.company_code}-FY{self.fiscal_year}-{self.token}"


#: Protocol 1.2, amended 2026-07-31 after acquisition (DECISIONS D-012).
#:
#: The FY2024 annual reports do not embed financial statements -- from that year the
#: statements are a separate 財務報告書 filing -- so three of those were added. The
#: study needs FY2024 numbers for its cross-period questions, and a real analyst
#: reads both documents too.
DECLARED_DOCUMENTS: Final[tuple[DeclaredDocument, ...]] = (
    DeclaredDocument("2412", 2023, "annual_report", "dev"),
    DeclaredDocument("1301", 2023, "annual_report", "dev"),
    DeclaredDocument("2330", 2023, "annual_report", "locked"),
    DeclaredDocument("2330", 2024, "annual_report", "locked", note="narrative only"),
    DeclaredDocument("2330", 2024, "financial_report", "locked"),
    DeclaredDocument("2317", 2023, "annual_report", "locked"),
    DeclaredDocument(
        "2317",
        2024,
        "annual_report",
        "locked",
        usable=False,
        note="unusable text layer: fonts lack a ToUnicode mapping, extraction yields glyph codes",
    ),
    DeclaredDocument("2317", 2024, "financial_report", "locked"),
    DeclaredDocument("2882", 2024, "annual_report", "locked", note="narrative only"),
    DeclaredDocument("2882", 2024, "financial_report", "locked"),
)

USABLE_DOCUMENTS: Final[tuple[DeclaredDocument, ...]] = tuple(
    document for document in DECLARED_DOCUMENTS if document.usable
)


# ------------------------------------------------------------------- question mix

#: Protocol 1.4. Fixed before annotation so the mix cannot be tilted toward
#: whatever the system happens to be good at.
LOCKED_TYPE_COUNTS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        "narrative_fact": 6,
        "table_cell": 5,
        "numeric_calculation": 5,
        "cross_period_comparison": 4,
        "chart_value_trend": 5,
        "cross_page": 4,
        "cross_document": 3,
        "unanswerable": 4,
    }
)

LOCKED_TOTAL: Final = 36
DEV_TOTAL: Final = 15
PROBE_COUNT: Final = 5
CHALLENGER_ITEMS: Final = 16

#: Protocol 1.4 / gate G2. These are the categories the study exists to move.
HARD_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "numeric_calculation",
        "cross_period_comparison",
        "chart_value_trend",
        "cross_page",
        "cross_document",
    }
)

#: 21 items. A single hard category has only 3-5 items, where one answer is worth
#: 20-33 percentage points, so G2 is judged on the pooled set first.
POOLED_HARD_SIZE: Final = sum(LOCKED_TYPE_COUNTS[t] for t in HARD_CATEGORIES)

#: Protocol 3.5. ``table_cell`` maps to the chart/table route because tabular
#: evidence is resolved by the same structured-extraction path as figures.
ROUTE_BY_QUESTION_TYPE: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "narrative_fact": "narrative",
        "table_cell": "chart",
        "numeric_calculation": "numeric",
        "cross_period_comparison": "numeric",
        "chart_value_trend": "chart",
        "cross_page": "narrative",
        "cross_document": "cross_modal",
        "unanswerable": "unanswerable",
    }
)


# ------------------------------------------------------------------ factor ladder

#: Protocol 2. F0 is the baseline; F7 is the candidate. Each step adds exactly one
#: factor, so metric deltas are attributable.
FACTOR_IDS: Final[tuple[str, ...]] = ("F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7")
BASELINE_FACTOR: Final = "F0"
CANDIDATE_FACTOR: Final = "F7"

#: Protocol 2.3. The chart challenger switches models only on a clear margin.
CHALLENGER_SWITCH_MIN_GAIN_PP: Final = 10.0


# -------------------------------------------------------------------------- gates


@dataclass(frozen=True, slots=True)
class Gates:
    """Protocol 4. Thresholds are fixed before the locked run and never relaxed."""

    # G2 -- both conditions must hold.
    pooled_hard_min_gain_pp: float = 10.0
    single_hard_min_gain_pp: float = 10.0
    # G3
    max_overall_regression_pp: float = 5.0
    # G4
    min_citation_validity: float = 0.90
    # G5
    min_numeric_route_accuracy: float = 0.90
    # G6
    min_route_accuracy: float = 0.85
    # G7
    max_over_answer_rate: float = 0.25
    min_refusal_precision: float = 0.80
    # G8
    min_probe_refusals: int = 4
    # G10 -- derived from the hardware (24 GB card minus desktop usage), not from
    # the chosen model, and therefore not relaxed if a model needs more.
    max_retrieval_p95_s: float = 3.0
    max_generation_p95_s: float = 60.0
    max_vram_peak_gb: float = 22.0


GATES: Final = Gates()


def consistency_problems(
    *,
    type_counts: Mapping[str, int] = LOCKED_TYPE_COUNTS,
    locked_total: int = LOCKED_TOTAL,
    hard_categories: Collection[str] = HARD_CATEGORIES,
    dev_codes: Collection[str] = DEV_COMPANY_CODES,
    locked_codes: Collection[str] = LOCKED_COMPANY_CODES,
    routes: Mapping[str, str] = ROUTE_BY_QUESTION_TYPE,
) -> list[str]:
    """Return every internal contradiction in a protocol definition.

    Defaults describe *this* study, so ``consistency_problems()`` checks the real
    constants. The parameters exist so the checks themselves are testable with
    deliberately broken inputs rather than only ever seen passing.
    """
    question_types = set(get_args(QuestionType))
    problems: list[str] = []

    if set(type_counts) != question_types:
        problems.append("locked type counts must cover exactly the QuestionType literals")
    if sum(type_counts.values()) != locked_total:
        problems.append(
            f"locked type counts sum to {sum(type_counts.values())}, not {locked_total}"
        )
    if not set(hard_categories).issubset(type_counts):
        problems.append("hard categories must all be question types")
    overlap = set(dev_codes) & set(locked_codes)
    if overlap:
        problems.append(f"dev and locked companies overlap: {sorted(overlap)}")
    if set(routes) != question_types:
        problems.append("every question type needs a gold route")
    unknown_routes = set(routes.values()) - set(get_args(Route))
    if unknown_routes:
        problems.append(f"unknown gold routes: {sorted(unknown_routes)}")
    return problems


_problems = consistency_problems()
if _problems:  # pragma: no cover - a contradiction here means the module is broken
    raise AssertionError("twfi.protocol is internally inconsistent: " + "; ".join(_problems))
