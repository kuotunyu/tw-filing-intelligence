"""Work out which filing a downloaded PDF actually is, from its own cover.

The MOPS document index is keyed by the *shareholders' meeting* year, not by the
fiscal year the report covers: searching 資料年度 112 returns the 民國111年度年報.
Naming files from the search field would therefore mislabel every document by one
year, and a mislabelled locked-set document is not a recoverable error -- it
silently changes what the study measured.

So the file names come from the report, not from the query. This module reads the
cover text and reports the company code and the fiscal year the document states
about itself.

Traditional Chinese covers write the year either in Arabic digits (``112年度年報``)
or in CJK digit sequences (``一一二年度年報``); both are handled. Compound forms
such as ``一百一十二年`` are not, because filings do not use them on covers -- and
an unparsed year is reported as unknown rather than guessed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ROC_EPOCH",
    "DocumentIdentity",
    "RenamePlan",
    "parse_cjk_digits",
    "find_company_code",
    "find_roc_year",
    "identify_cover_text",
    "read_cover_text",
    "plan_renames",
]

#: 民國 1 == 1912 CE, so a 民國 year plus 1911 gives the Western year.
ROC_EPOCH = 1911

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

#: A plausible 民國 year for a filing this study could use. Outside this range the
#: match is far more likely to be a page number or a phone fragment.
_MIN_ROC_YEAR = 90
_MAX_ROC_YEAR = 130


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


def find_company_code(text: str) -> str | None:
    """Return the four-digit listing code printed on the cover, if present."""
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
    """What a document says about itself."""

    company_code: str | None
    roc_year: int | None

    @property
    def fiscal_year(self) -> int | None:
        return None if self.roc_year is None else self.roc_year + ROC_EPOCH

    @property
    def is_complete(self) -> bool:
        return self.company_code is not None and self.roc_year is not None

    def expected_filename(self, suffix: str = ".pdf") -> str | None:
        """The name this study requires for this document, or ``None`` if unknown."""
        if not self.is_complete:
            return None
        return f"{self.company_code}-FY{self.fiscal_year}-AR{suffix}"


def identify_cover_text(text: str) -> DocumentIdentity:
    """Extract the company code and fiscal year from cover text."""
    return DocumentIdentity(company_code=find_company_code(text), roc_year=find_roc_year(text))


def read_cover_text(path: Path, pages: int = 3) -> str:
    """Return the text of the first few pages, or ``""`` if the file is unreadable.

    The year is sometimes on the cover and sometimes on the title page behind it,
    so a few pages are read rather than only the first.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        return ""
    try:
        with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
            limit = min(pages, document.page_count)
            return "\n".join(
                str(document.load_page(index).get_text())
                for index in range(limit)
            )
    except Exception:  # a corrupt download must not abort the whole scan
        return ""


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

    A file whose identity is readable but that the protocol never declared is
    reported as undeclared rather than renamed: quietly accepting an extra document
    would change the study's document set without a protocol amendment.
    """
    plans: list[RenamePlan] = []
    for path in sorted(paths):
        identity = identify_cover_text(cover_reader(path))
        target = identity.expected_filename(path.suffix.lower() or ".pdf")

        problem = ""
        if target is None:
            missing = []
            if identity.company_code is None:
                missing.append("company code")
            if identity.roc_year is None:
                missing.append("fiscal year")
            problem = "could not read " + " and ".join(missing) + " from the first pages"

        plans.append(
            RenamePlan(
                path=path,
                identity=identity,
                target_name=target,
                declared=target in declared_filenames,
                problem=problem,
            )
        )
    return plans
