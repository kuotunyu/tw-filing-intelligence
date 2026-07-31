"""Is a downloaded filing actually usable as evidence?

Two failure modes showed up on the first real batch, and neither is visible from
a file listing:

* **A broken text layer.** Some filings embed fonts without a usable ToUnicode
  CMap, so native extraction returns glyph codes rather than characters. The
  document looks fine to a human and yields tens of thousands of characters of
  mojibake to a parser. ``2317-FY2024`` extracted 118,681 characters in which the
  string 鴻海 does not appear even once.
* **Statements filed separately.** From FY2024 the 股東會年報 does not embed the
  financial statements; they are a separate filing (資料類型「財務報告書」→
  「IFRSs合併財報」), and the MOPS result page offers a 「查詢財務報告書」button for
  exactly that reason. ``2330-FY2024`` has 公司治理 and 風險事項 but no
  合併資產負債表, and MOPS lists only one file for that year -- so this is a
  different filing, not a split one.

Both would silently corrupt the study: unusable text would look like a retrieval
failure, and a missing statements section would look like the numeric route being
unable to answer. So they are detected up front and named for what they are.

Detection is by anchor terms rather than by encoding inspection: any Chinese annual
report contains 公司 and 財務 somewhere. If a 100-page filing contains neither, the
text layer is not usable, whatever the byte-level cause.

A third mode showed up once the parser was pointed at these files: **partial**
breakage. ``2317-FY2023`` passes a document-level anchor check, yet its headings
extract as ``Ψҗᇙ୍ܺ`` -- some of its embedded fonts map and some do not. Judging the
document as a whole hides that, so readability is measured per page and reported as a
ratio. A filing where a third of the pages are unreadable can still host questions,
but only if that is known when the questions are written.

A fourth mode was invisible to this module until an annotation tool tripped over it:
**a page with no text layer at all**. The first version divided readable pages by
``pages_with_text``, so a page yielding zero characters was dropped from the denominator
and could never lower the score. The instrument was structurally unable to report an
image-only page, and eleven consecutive ones hid inside ``2330-FY2024-FS`` -- a document
this module called 100% readable. By position in a 財務報告書 those eleven pages are the
four primary statements, sitting after the auditor's report and before the notes.

The two modes are different failures and are now measured separately:

* ``readable_ratio`` divides by *all* pages, so silence counts against a document.
* ``legible_text_ratio`` keeps the old denominator, which answers a narrower and still
  useful question: of the pages that do carry text, how many are legible? Glyph
  breakage moves this one; a missing text layer does not.

Contiguous runs of image-only pages are reported too, because the length matters more
than the count. Scattered blank pages are covers and dividers. A run of a dozen is a
structural block of the filing that no text-based route can reach.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "ANCHOR_TERMS",
    "STATEMENT_TERMS",
    "MIN_PAGES_FOR_JUDGEMENT",
    "MIN_READABLE_PAGE_RATIO",
    "MIN_IMAGE_ONLY_RUN",
    "Verdict",
    "DocumentQuality",
    "image_only_runs",
    "assess_pages",
]

#: Terms common enough that a readable page of a Chinese filing contains at least
#: one. Their total absence means the text layer cannot be read, not that the report
#: is unusual.
#:
#: The list deliberately spans both registers. Narrative vocabulary alone
#: (公司 / 股東 / 董事) marked pure statement pages as unreadable, because a page that
#: is nothing but 合併資產負債表 and figures contains none of it.
ANCHOR_TERMS: tuple[str, ...] = (
    # narrative
    "公司",
    "年度",
    "財務",
    "營業",
    "股東",
    "董事",
    # statements and notes
    "合併",
    "資產",
    "負債",
    "附註",
    "單位",
)

#: The sections the numeric and table routes depend on.
STATEMENT_TERMS: tuple[str, ...] = ("合併資產負債表", "合併綜合損益表", "會計師查核報告")

#: Below this, absence of anchor terms is not strong evidence of anything.
MIN_PAGES_FOR_JUDGEMENT = 20

#: A page with text but no anchor term is treated as unreadable. Below this share of
#: readable pages the document is only partly usable.
MIN_READABLE_PAGE_RATIO = 0.6

#: Consecutive pages with no text layer at all. One or two are covers and section
#: dividers; this many in a row is a block of the filing that no text-based route can
#: reach. 2330-FY2024-FS has a run of 13 where its four primary statements should be.
MIN_IMAGE_ONLY_RUN = 5

Verdict = Literal[
    "usable",
    "unusable_text_layer",
    "partially_unusable_text_layer",
    "statements_not_machine_readable",
    "missing_financial_statements",
    "too_short",
]


@dataclass(frozen=True, slots=True)
class DocumentQuality:
    """What one document contains, and whether it can serve as evidence."""

    doc_id: str
    pages: int
    characters: int
    readable_pages: int = 0
    pages_with_text: int = 0
    anchor_hits: Mapping[str, int] = field(default_factory=dict)
    statement_pages: Mapping[str, int | None] = field(default_factory=dict)
    #: Inclusive 1-based ``(first, last)`` spans of pages with no text layer at all.
    image_only_runs: tuple[tuple[int, int], ...] = ()
    verdict: Verdict = "usable"
    reasons: tuple[str, ...] = ()

    @property
    def readable_ratio(self) -> float:
        """Share of *all* pages that are legible.

        Divides by every page, so a page with no text layer counts against the
        document. The earlier version divided by ``pages_with_text`` and therefore
        could not report an image-only page at all.
        """
        return self.readable_pages / self.pages if self.pages else 0.0

    @property
    def legible_text_ratio(self) -> float:
        """Of the pages that carry text, the share that is legible.

        Isolates glyph breakage from a missing text layer: mojibake moves this number,
        an image-only page does not.
        """
        return self.readable_pages / self.pages_with_text if self.pages_with_text else 0.0

    @property
    def image_only_pages(self) -> int:
        return self.pages - self.pages_with_text

    @property
    def longest_image_only_run(self) -> int:
        return max((last - first + 1 for first, last in self.image_only_runs), default=0)

    @property
    def is_usable(self) -> bool:
        return self.verdict == "usable"

    @property
    def has_financial_statements(self) -> bool:
        return any(page is not None for page in self.statement_pages.values())

    @property
    def chars_per_page(self) -> float:
        return self.characters / self.pages if self.pages else 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "doc_id": self.doc_id,
            "pages": self.pages,
            "characters": self.characters,
            "chars_per_page": round(self.chars_per_page, 1),
            "pages_with_text": self.pages_with_text,
            "image_only_pages": self.image_only_pages,
            "image_only_runs": [list(span) for span in self.image_only_runs],
            "longest_image_only_run": self.longest_image_only_run,
            "readable_pages": self.readable_pages,
            "readable_ratio": round(self.readable_ratio, 3),
            "legible_text_ratio": round(self.legible_text_ratio, 3),
            "anchor_hits": dict(self.anchor_hits),
            "statement_pages": dict(self.statement_pages),
            "verdict": self.verdict,
            "reasons": list(self.reasons),
        }


def image_only_runs(page_texts: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Inclusive 1-based spans of consecutive pages with no text layer at all.

    Length is what carries meaning: two blank pages are a cover and a divider, a dozen
    is a block of the filing that text extraction cannot reach.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, text in enumerate(page_texts, start=1):
        if text.strip():
            if start is not None:
                runs.append((start, index - 1))
                start = None
        elif start is None:
            start = index
    if start is not None:
        runs.append((start, len(page_texts)))
    return tuple(runs)


def assess_pages(doc_id: str, page_texts: Sequence[str]) -> DocumentQuality:
    """Judge a document from its already-extracted page text.

    Pure, so the judgement is tested directly rather than only through PDFs.
    """
    pages = len(page_texts)
    characters = sum(len(text) for text in page_texts)

    anchor_hits = {term: sum(1 for text in page_texts if term in text) for term in ANCHOR_TERMS}
    statement_pages: dict[str, int | None] = {}
    for term in STATEMENT_TERMS:
        statement_pages[term] = next(
            (index for index, text in enumerate(page_texts, start=1) if term in text), None
        )

    pages_with_text = sum(1 for text in page_texts if text.strip())
    readable_pages = sum(
        1 for text in page_texts if text.strip() and any(term in text for term in ANCHOR_TERMS)
    )
    runs = image_only_runs(page_texts)
    longest_run = max((last - first + 1 for first, last in runs), default=0)
    # Only the narrow ratio is needed here; DocumentQuality.readable_ratio divides by
    # every page, which is the headline number but not what any verdict turns on.
    legible_text_ratio = readable_pages / pages_with_text if pages_with_text else 0.0

    reasons: list[str] = []
    verdict: Verdict = "usable"

    if pages < MIN_PAGES_FOR_JUDGEMENT:
        verdict = "too_short"
        reasons.append(f"only {pages} pages; an annual report should be far longer")
    elif sum(anchor_hits.values()) == 0:
        verdict = "unusable_text_layer"
        reasons.append(
            f"extracted {characters} characters but none of {list(ANCHOR_TERMS)} appear; "
            "the embedded fonts most likely lack a usable ToUnicode mapping"
        )
    elif legible_text_ratio < MIN_READABLE_PAGE_RATIO:
        # Judged on the narrow ratio on purpose: this verdict names glyph breakage, and
        # blaming it for pages that carry no text at all would misdescribe the cause.
        verdict = "partially_unusable_text_layer"
        reasons.append(
            f"only {readable_pages}/{pages_with_text} pages with text contain a recognisable "
            f"term ({legible_text_ratio:.0%}); some embedded fonts map and some do not, so parts "
            "of this filing extract as glyph codes"
        )
    elif longest_run >= MIN_IMAGE_ONLY_RUN:
        verdict = "statements_not_machine_readable"
        spans = ", ".join(f"pp.{first}-{last}" for first, last in runs if last - first + 1 >= 3)
        reasons.append(
            f"{longest_run} consecutive pages carry no text layer ({spans}); a run this long "
            "is a structural block of the filing, not a divider. In a 財務報告書 the block "
            "between the auditor's report and the notes is the four primary statements, so "
            "table and numeric questions cannot be sourced from them -- though figures "
            "restated in the notes may still be readable"
        )
    elif all(page is None for page in statement_pages.values()):
        verdict = "missing_financial_statements"
        reasons.append(
            f"none of {list(STATEMENT_TERMS)} appear; from FY2024 the 股東會年報 does not "
            "embed the statements -- acquire the separate 財務報告書 "
            "(資料類型「財務報告書」→ 細節「IFRSs合併財報」→ 季別「第四季」)"
        )

    return DocumentQuality(
        doc_id=doc_id,
        pages=pages,
        characters=characters,
        readable_pages=readable_pages,
        pages_with_text=pages_with_text,
        anchor_hits=anchor_hits,
        statement_pages=statement_pages,
        image_only_runs=runs,
        verdict=verdict,
        reasons=tuple(reasons),
    )
