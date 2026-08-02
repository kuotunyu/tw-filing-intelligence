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
    "CHALLENGER_STATUS",
    "CHALLENGER_CANCELLED_REASON",
    "HARD_CATEGORIES",
    "SINGLE_GATE_CATEGORIES",
    "POOLED_HARD_SIZE",
    "ROUTE_BY_QUESTION_TYPE",
    "FACTOR_IDS",
    "TOP_K_RETRIEVE",
    "TOP_K_RERANK",
    "BASELINE_TOP_K",
    "RRF_K",
    "RECALL_AT",
    "MRR_AT",
    "COVERAGE_AT",
    "BASELINE_FACTOR",
    "CANDIDATE_FACTOR",
    "Gates",
    "GATES",
    "CHALLENGER_SWITCH_MIN_GAIN_PP",
    "consistency_problems",
]

PROTOCOL_VERSION: Final = "1.0.0"

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


#: Protocol 1.2, amended 2026-07-31 after acquisition (DECISIONS D-012, D-013).
#:
#: Two amendments, both driven by measurement rather than preference:
#:   D-012  From FY2024 the 股東會年報 does not embed financial statements, so three
#:          FY2024 財務報告書 filings were added.
#:   D-013  Native text extraction fails on both 鴻海 annual reports. ``usable``
#:          records the measured verdict; question sourcing draws only on
#:          USABLE_DOCUMENTS, but the unusable ones stay declared so the finding keeps
#:          its SHA-256 and its place in the report.
#:
#: Readable-page ratios measured by ``scripts/check_document_quality.py``. That
#: instrument has been wrong twice, in opposite directions, and both corrections are
#: recorded because the ratios below are only as good as the thing measuring them:
#:   1. An anchor list of narrative vocabulary only condemned 1301 and understated 2330,
#:      because a page that is nothing but 合併資產負債表 and figures contains no
#:      narrative words. Widening the anchors put 1301 at 96%.
#:   2. The ratio divided by pages *with text*, so a page yielding zero characters left
#:      the denominator along with the numerator and could never lower the score. It
#:      reported 2330-FY2024-FS as 100% readable while nine consecutive pages -- its four
#:      primary statements -- had no text layer at all. D-017.
#:
#: Which question types each filing can actually source is measured separately, by
#: ``scripts/check_question_sources.py``: a single usable/unusable flag is too coarse to
#: annotate against when a document's notes are readable and its statements are not.
DECLARED_DOCUMENTS: Final[tuple[DeclaredDocument, ...]] = (
    DeclaredDocument("2412", 2023, "annual_report", "dev", note="95% of pages readable"),
    DeclaredDocument("1301", 2023, "annual_report", "dev", note="96% of pages readable"),
    DeclaredDocument("2330", 2023, "annual_report", "locked", note="98% of pages readable"),
    DeclaredDocument("2330", 2024, "annual_report", "locked", note="narrative only; 100% readable"),
    DeclaredDocument(
        "2330",
        2024,
        "financial_report",
        "locked",
        note=(
            "91% readable; the four primary statements (pp.7-15) have no text layer, so "
            "figures are sourceable only from the notes -- D-017"
        ),
    ),
    DeclaredDocument(
        "2317",
        2023,
        "annual_report",
        "locked",
        usable=False,
        note="unusable text layer: only 148/707 pages (21%) extract readable text",
    ),
    DeclaredDocument(
        "2317",
        2024,
        "annual_report",
        "locked",
        usable=False,
        note="unusable text layer: 0% readable; fonts lack a ToUnicode mapping",
    ),
    DeclaredDocument("2317", 2024, "financial_report", "locked", note="95% readable"),
    DeclaredDocument("2882", 2024, "annual_report", "locked", note="narrative only; 100% readable"),
    DeclaredDocument("2882", 2024, "financial_report", "locked", note="99% readable"),
)

USABLE_DOCUMENTS: Final[tuple[DeclaredDocument, ...]] = tuple(
    document for document in DECLARED_DOCUMENTS if document.usable
)


# ------------------------------------------------------------------- question mix

#: Protocol 1.4. Fixed before annotation so the mix cannot be tilted toward
#: whatever the system happens to be good at.
#:
#: Amended 2026-07-31, before freeze, on a measurement rather than a result (D-020):
#: chart_value_trend drops 5 to 2 because the locked filings contain four genuine charts,
#: all 台積電, all on two pages. Five items drawn from them would be five readings of the
#: same two infographics -- correlated enough that one capability decides the whole
#: category. Two items, one per distinct chart, is what the corpus actually supports.
LOCKED_TYPE_COUNTS: Final[MappingProxyType[str, int]] = MappingProxyType(
    {
        "narrative_fact": 6,
        "table_cell": 5,
        "numeric_calculation": 5,
        "cross_period_comparison": 4,
        "chart_value_trend": 2,
        "cross_page": 4,
        "cross_document": 3,
        "unanswerable": 4,
    }
)

LOCKED_TOTAL: Final = 33
DEV_TOTAL: Final = 15
PROBE_COUNT: Final = 5
CHALLENGER_ITEMS: Final = 16

#: Protocol 2.3 as amended by D-021. The challenger will not run, and this says so in code
#: so that an unbuilt set does not read as unfinished work someone should go and finish.
#:
#: It was cancelled for want of material, not because of a result: nothing was ever
#: compared. Its conclusion is identical to the fallback branch fixed in advance -- 27B
#: serves every route -- so cancelling cannot have favoured this study.
CHALLENGER_STATUS: Final = "cancelled"
CHALLENGER_CANCELLED_REASON: Final = (
    "The dev filings contain no charts. All 20 geometry positives in 2412-FY2023-AR and "
    "1301-FY2023-AR were inspected one by one and all 20 are tables -- mostly the "
    "diagonal-split header 「項目＼年度」, the rest seal stamps -- so the 16 dev chart crops "
    "the comparison needs cannot be written. Selecting a model on the locked set's 4 charts "
    "is forbidden, and running the comparison on dev tables would report a table-reading "
    "result as a chart-route decision."
)

#: Protocol 1.4 / gate G2. These are the categories the study exists to move, and the
#: pooled set G2's first condition is judged on.
HARD_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "numeric_calculation",
        "cross_period_comparison",
        "chart_value_trend",
        "cross_page",
        "cross_document",
    }
)

#: G2's *second* condition -- at least one single category improving by the threshold --
#: may only be judged on categories large enough for a threshold to mean something.
#:
#: chart_value_trend is excluded (2026-07-31, D-020). At two items one answer is fifty
#: percentage points, so a category that small would let a single lucky reading satisfy a
#: gate on its own. It stays in the pooled set, where it contributes evidence without
#: being able to decide anything alone, and it is still scored and reported.
SINGLE_GATE_CATEGORIES: Final[frozenset[str]] = HARD_CATEGORIES - {"chart_value_trend"}

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

#: Protocol 2.5. Fixed on dev, written down before the locked run, identical for F0 through F7 --
#: retuning them per rung would move retrieval strength between rungs and make each factor's
#: delta unattributable.
TOP_K_RETRIEVE: Final = 20
TOP_K_RERANK: Final = 5
BASELINE_TOP_K: Final = 5
RRF_K: Final = 60

#: Protocol 3.2. The cutoffs the four retrieval metrics are defined at.
#:
#: These are constants, not options. `eval_retrieval.py` spent its development reporting
#: page-level recall at 10 and 20 -- adjacent quantities that the study does not gate on -- and
#: `fetch_depth` was chosen against one of them. Naming the registered cutoffs here is what stops
#: a convenient cutoff being reported as if it were the pre-registered one.
RECALL_AT: Final = 5
MRR_AT: Final = 10
#: Complete-evidence and cross-page coverage are both judged over the same top-5 as Recall@5.
COVERAGE_AT: Final = 5


# -------------------------------------------------------------------------- gates


@dataclass(frozen=True, slots=True)
class Gates:
    """Protocol 4. Thresholds are fixed before the locked run and never relaxed."""

    # G2 -- both conditions must hold.
    #
    # Raised from 10.0 on 2026-07-31 (D-020). Reducing chart_value_trend from five items
    # to two shrank the pooled hard set from 21 to 18, and at 10 points a gain of 1.8
    # items would have passed -- meaning two extra correct answers could clear a gate
    # that was written to need more than two. 15 points over 18 items needs three. The
    # change makes GO harder rather than easier, which is the only direction a threshold
    # may move once the data exist.
    pooled_hard_min_gain_pp: float = 15.0
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
    if not SINGLE_GATE_CATEGORIES.issubset(hard_categories):
        problems.append("single-gate categories must be a subset of the hard categories")
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
