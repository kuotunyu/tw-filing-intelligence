"""Work out which filing a downloaded PDF actually is.

The MOPS document index is keyed by the *shareholders' meeting* year, not by the
fiscal year the report covers: searching 資料年度 112 returns the FY2022 annual
report, presented at the 2023 AGM. Naming files from the search field would
therefore mislabel every document by one year, and a mislabelled locked-set
document is not a recoverable error -- it silently changes what the study measured.

So identity comes from the document, using two independent sources:

* **The MOPS filename**, e.g. ``2022_2412_20230526F04_20260731_021308.pdf`` --
  fiscal year (Western), company code, filing date, document-type code. This is
  assigned by MOPS and is the authoritative source.
* **The cover text**, e.g. ``一一一年度年報`` -- corroboration. Covers are often
  designed artwork, so the listing code frequently has no text layer at all; the
  year usually does.

When both are readable and disagree, that is reported as a conflict rather than
resolved by preferring one. Silently picking a winner is how a mislabelled document
gets into a locked set.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ROC_EPOCH",
    "MopsFilename",
    "DocumentIdentity",
    "RenamePlan",
    "parse_cjk_digits",
    "parse_mops_filename",
    "find_company_code",
    "find_roc_year",
    "identify",
    "read_cover_text",
    "pdf_candidates",
    "plan_renames",
]

#: 民國 1 == 1912 CE, so a 民國 year plus 1911 gives the Western year.
ROC_EPOCH = 1911

#: A plausible fiscal year for a filing this study could use. Outside this range a
#: match is far more likely to be a page number or part of a phone number.
_MIN_FISCAL_YEAR = 2001
_MAX_FISCAL_YEAR = 2041

_CJK_DIGITS = {
    "〇": 0,
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

#: MOPS uses two naming schemes, one per 資料類型. Observed 2026-07-31:
#:   股東會年報   2024_2330_20250603F04.pdf     year _ code _ filingdate dtype
#:   財務報告書   202404_2317_AI1_...pdf        year quarter _ code _ dtype
_ANNUAL_FILENAME = re.compile(
    r"^(?P<year>\d{4})_(?P<code>\d{4})_(?P<filed>\d{8})(?P<dtype>[A-Z]\d{2})",
)
_FINANCIAL_FILENAME = re.compile(
    r"^(?P<year>\d{4})(?P<quarter>0[1-4])_(?P<code>\d{4})_(?P<dtype>[A-Z]{1,2}\d)",
)

#: 資料細節說明 codes this study accepts, and which declared document kind each is.
#: An allowlist rather than a prefix rule: F01 開會通知 and F19 僅永續專章 also start
#: with F, and mislabelling one of those as an annual report would be worse than
#: refusing to name it.
_ANNUAL_DTYPES = frozenset({"F04", "F18", "F11"})
_FINANCIAL_DTYPES = frozenset({"AI1", "AI2", "AI3", "AI4", "AI5", "AI6", "A01", "A02"})

#: The token that appears in a declared doc_id for each kind.
_KIND_TOKEN: dict[str, str] = {"annual_report": "AR", "financial_report": "FS"}

_CODE_PATTERNS = (
    re.compile(r"股票代[碼號]\s*[:：]?\s*(\d{4})"),
    re.compile(r"證券代[碼號]\s*[:：]?\s*(\d{4})"),
    re.compile(r"公司代[碼號]\s*[:：]?\s*(\d{4})"),
)

_CJK_YEAR = f"[{''.join(_CJK_DIGITS)}]{{2,3}}"
_YEAR_PATTERNS = (
    re.compile(r"民國\s*(\d{2,3})\s*年度"),
    re.compile(r"(\d{2,3})\s*年度年報"),
    re.compile(rf"民國\s*({_CJK_YEAR})\s*年度"),
    re.compile(rf"({_CJK_YEAR})\s*年度年報"),
)

_MIN_ROC_YEAR = _MIN_FISCAL_YEAR - ROC_EPOCH
_MAX_ROC_YEAR = _MAX_FISCAL_YEAR - ROC_EPOCH


def parse_cjk_digits(text: str) -> int | None:
    """Parse a CJK digit sequence such as ``一一二`` into ``112``.

    Returns ``None`` for anything that is not a pure digit sequence, including
    compound forms like ``一百一十二``.
    """
    if not text:
        return None
    value = 0
    for character in text:
        digit = _CJK_DIGITS.get(character)
        if digit is None:
            return None
        value = value * 10 + digit
    return value


@dataclass(frozen=True, slots=True)
class MopsFilename:
    """The fields MOPS encodes into a downloaded filing's name."""

    fiscal_year: int
    company_code: str
    dtype: str
    kind: str | None = None
    filed_on: str = ""
    quarter: int | None = None


def parse_mops_filename(name: str) -> MopsFilename | None:
    """Parse either MOPS naming scheme, or ``None`` if the name is neither.

    A browser-renamed copy (``report (1).pdf``) returns ``None``, leaving the cover
    as the only source. ``kind`` is ``None`` for a recognised name whose 資料細節說明
    code is not one this study accepts -- an 開會通知 is refused rather than filed as
    an annual report.
    """
    annual = _ANNUAL_FILENAME.match(name)
    if annual is not None:
        year = int(annual.group("year"))
        if not _MIN_FISCAL_YEAR <= year <= _MAX_FISCAL_YEAR:
            return None
        return MopsFilename(
            fiscal_year=year,
            company_code=annual.group("code"),
            dtype=annual.group("dtype"),
            kind=_kind_for(annual.group("dtype")),
            filed_on=annual.group("filed"),
        )

    financial = _FINANCIAL_FILENAME.match(name)
    if financial is not None:
        year = int(financial.group("year"))
        if not _MIN_FISCAL_YEAR <= year <= _MAX_FISCAL_YEAR:
            return None
        return MopsFilename(
            fiscal_year=year,
            company_code=financial.group("code"),
            dtype=financial.group("dtype"),
            kind=_kind_for(financial.group("dtype")),
            quarter=int(financial.group("quarter")),
        )

    return None


def _kind_for(dtype: str) -> str | None:
    if dtype in _ANNUAL_DTYPES:
        return "annual_report"
    if dtype in _FINANCIAL_DTYPES:
        return "financial_report"
    return None


def find_company_code(text: str) -> str | None:
    """Return the four-digit listing code printed on the cover, if present.

    Only a *labelled* code counts. Filings are full of four-digit numbers, and an
    unlabelled match would be a coin flip.
    """
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def find_roc_year(text: str) -> int | None:
    """Return the 民國 year the document says it covers, if it states one."""
    for pattern in _YEAR_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1)
            year = int(raw) if raw.isdigit() else parse_cjk_digits(raw)
            if year is not None and _MIN_ROC_YEAR <= year <= _MAX_ROC_YEAR:
                return year
    return None


@dataclass(frozen=True, slots=True)
class DocumentIdentity:
    """What a document is, and which sources said so."""

    company_code: str | None = None
    fiscal_year: int | None = None
    roc_year: int | None = None
    kind: str | None = None
    evidence: tuple[str, ...] = ()
    conflict: str = ""

    @property
    def is_complete(self) -> bool:
        return (
            self.company_code is not None
            and self.fiscal_year is not None
            and self.kind in _KIND_TOKEN
            and not self.conflict
        )

    def missing(self) -> list[str]:
        """Which parts of the identity could not be established."""
        gaps = []
        if self.company_code is None:
            gaps.append("company code")
        if self.fiscal_year is None:
            gaps.append("fiscal year")
        if self.kind not in _KIND_TOKEN:
            gaps.append("document kind")
        return gaps

    def expected_filename(self, suffix: str = ".pdf") -> str | None:
        """The name this study requires, or ``None`` if identity is not established."""
        if not self.is_complete:
            return None
        token = _KIND_TOKEN[str(self.kind)]
        return f"{self.company_code}-FY{self.fiscal_year}-{token}{suffix}"

    def describe(self) -> str:
        code = self.company_code or "?"
        roc = f"民國 {self.roc_year} 年度" if self.roc_year else "民國 ? 年度"
        year = f"FY{self.fiscal_year}" if self.fiscal_year else "FY?"
        kind = _KIND_TOKEN.get(str(self.kind), "?")
        sources = "+".join(self.evidence) if self.evidence else "none"
        return f"代號 {code} / {roc} / {year} / {kind}  (from {sources})"


def identify(filename: str, cover_text: str) -> DocumentIdentity:
    """Combine the MOPS filename and the cover into one identity.

    The filename wins where both are available, because MOPS assigns it; the cover
    is used to fill gaps and to detect disagreement.
    """
    from_name = parse_mops_filename(filename)
    cover_code = find_company_code(cover_text)
    cover_roc = find_roc_year(cover_text)
    cover_year = None if cover_roc is None else cover_roc + ROC_EPOCH

    evidence: list[str] = []
    if from_name is not None:
        evidence.append("filename")
    if cover_code is not None or cover_roc is not None:
        evidence.append("cover")

    conflicts: list[str] = []
    if from_name is not None and cover_year is not None and from_name.fiscal_year != cover_year:
        conflicts.append(
            f"filename says FY{from_name.fiscal_year} but the cover says FY{cover_year}"
        )
    if from_name is not None and cover_code is not None and from_name.company_code != cover_code:
        conflicts.append(f"filename says {from_name.company_code} but the cover says {cover_code}")

    return DocumentIdentity(
        company_code=from_name.company_code if from_name else cover_code,
        fiscal_year=from_name.fiscal_year if from_name else cover_year,
        roc_year=cover_roc
        if cover_roc is not None
        else (from_name.fiscal_year - ROC_EPOCH if from_name else None),
        kind=from_name.kind if from_name else None,
        evidence=tuple(evidence),
        conflict="; ".join(conflicts),
    )


def read_cover_text(path: Path, pages: int = 3) -> str:
    """Return the text of the first few pages, or ``""`` if the file is unreadable.

    The year sits on the cover on some filings and on the title page behind it on
    others, so a few pages are read rather than only the first.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        return ""
    try:
        with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
            limit = min(pages, document.page_count)
            return "\n".join(str(document.load_page(index).get_text()) for index in range(limit))
    except Exception:  # a corrupt download must not abort the whole scan
        return ""


def pdf_candidates(directory: Path) -> list[Path]:
    """Every PDF in ``directory``, deduplicated and in a stable order.

    Globbing ``*.pdf`` and ``*.PDF`` separately returns the same file twice on a
    case-insensitive filesystem, which is what Windows has.
    """
    if not directory.is_dir():
        return []
    seen: dict[Path, Path] = {}
    for path in directory.iterdir():
        if path.is_file() and path.suffix.lower() == ".pdf":
            seen.setdefault(path.resolve(), path)
    return sorted(seen.values())


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """What one downloaded file is, and what it should be called."""

    path: Path
    identity: DocumentIdentity
    target_name: str | None
    declared: bool
    problem: str = ""

    @property
    def needs_rename(self) -> bool:
        return self.target_name is not None and self.path.name != self.target_name

    @property
    def is_ready(self) -> bool:
        return self.target_name is not None and self.declared and not self.problem


def plan_renames(
    paths: Iterable[Path],
    declared_filenames: set[str],
    *,
    cover_reader: Callable[[Path], str] = read_cover_text,
) -> list[RenamePlan]:
    """Identify each file and say what it should be called.

    A file whose identity reads cleanly but that the protocol never declared is
    reported as undeclared rather than renamed: quietly accepting an extra document
    would change the study's document set without a protocol amendment.

    A file that *already* carries a declared name needs no identification. Renaming
    discards the MOPS filename, which is the strongest identity evidence, so a
    second pass would otherwise report every previously-renamed file as unreadable.
    The declaration is the identity at that point, and the digest in
    ``acquisition.lock.yaml`` is what guards it.
    """
    plans: list[RenamePlan] = []
    for path in sorted(paths):
        if path.name in declared_filenames:
            plans.append(
                RenamePlan(
                    path=path,
                    identity=DocumentIdentity(evidence=("declared name",)),
                    target_name=path.name,
                    declared=True,
                )
            )
            continue

        found = identify(path.name, cover_reader(path))
        target = found.expected_filename(path.suffix.lower() or ".pdf")

        problem = found.conflict
        if not problem and target is None:
            problem = (
                "could not read "
                + " and ".join(found.missing())
                + " from the filename or the first pages"
            )

        plans.append(
            RenamePlan(
                path=path,
                identity=found,
                target_name=target,
                declared=target in declared_filenames,
                problem=problem,
            )
        )
    return plans
