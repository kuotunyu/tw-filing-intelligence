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

#: MOPS names downloads ``<fiscal year>_<code>_<filing date><dtype>_<download stamp>``.
_MOPS_FILENAME = re.compile(
    r"^(?P<year>\d{4})_(?P<code>\d{4})_(?P<filed>\d{8})(?P<dtype>[A-Z][A-Za-z0-9]{1,3})",
)

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
    filed_on: str
    dtype: str


def parse_mops_filename(name: str) -> MopsFilename | None:
    """Parse a MOPS download filename, or ``None`` if it is not one.

    A browser-renamed copy (``report (1).pdf``) simply returns ``None``, leaving
    the cover as the only source.
    """
    match = _MOPS_FILENAME.match(name)
    if match is None:
        return None
    year = int(match.group("year"))
    if not _MIN_FISCAL_YEAR <= year <= _MAX_FISCAL_YEAR:
        return None
    return MopsFilename(
        fiscal_year=year,
        company_code=match.group("code"),
        filed_on=match.group("filed"),
        dtype=match.group("dtype"),
    )


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
    evidence: tuple[str, ...] = ()
    conflict: str = ""

    @property
    def is_complete(self) -> bool:
        return self.company_code is not None and self.fiscal_year is not None and not self.conflict

    def expected_filename(self, suffix: str = ".pdf") -> str | None:
        """The name this study requires, or ``None`` if identity is not established."""
        if not self.is_complete:
            return None
        return f"{self.company_code}-FY{self.fiscal_year}-AR{suffix}"

    def describe(self) -> str:
        code = self.company_code or "?"
        roc = f"民國 {self.roc_year} 年度" if self.roc_year else "民國 ? 年度"
        year = f"FY{self.fiscal_year}" if self.fiscal_year else "FY?"
        sources = "+".join(self.evidence) if self.evidence else "none"
        return f"代號 {code} / {roc} / {year}  (from {sources})"


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
    """
    plans: list[RenamePlan] = []
    for path in sorted(paths):
        found = identify(path.name, cover_reader(path))
        target = found.expected_filename(path.suffix.lower() or ".pdf")

        problem = found.conflict
        if not problem and target is None:
            missing = []
            if found.company_code is None:
                missing.append("company code")
            if found.fiscal_year is None:
                missing.append("fiscal year")
            problem = (
                "could not read " + " and ".join(missing) + " from the filename or the first pages"
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
