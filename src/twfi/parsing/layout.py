"""The candidate parser: structure from font statistics and geometry.

Taiwanese annual reports are digital-born, so the text layer is already complete
and OCR is not the bottleneck. What plain extraction loses is *structure* --
which line is a heading, which section a paragraph belongs to, which lines are
repeated page furniture, and what the reading order is. That is what this module
recovers, using only PyMuPDF's span geometry and font metadata.

Design choices worth knowing about:

* **Rule-based and deterministic.** Same PDF in, same blocks out, no model
  weights to download and no inference variance. The cost is that the conclusion
  cannot be generalised to learned layout models; see DECISIONS D-002, and the
  report must state that limitation.
* **Structure detection is pure.** :func:`classify_pages` and its helpers take
  plain :class:`~twfi.parsing.types.Line` objects, so the heading, furniture, and
  section logic is tested directly rather than only through PDF fixtures.
* **Single-column reading order.** Filings are overwhelmingly single-column body
  text with full-width tables. Multi-column layouts are a known limitation rather
  than a silent failure -- :func:`reading_order` sorts within y-bands, which
  degrades to left-to-right on a genuine two-column page.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from twfi.errors import ParsingError
from twfi.parsing.normalise import normalise
from twfi.parsing.types import BBox, Block, Line, ParsedDocument, ParsedPage, Span

__all__ = [
    "PARSER_NAME",
    "LayoutConfig",
    "RawPage",
    "detect_numbering_level",
    "looks_tabular",
    "body_font_size",
    "body_font_sizes",
    "repeated_furniture",
    "reading_order",
    "classify_pages",
    "extract_raw_pages",
    "parse_layout",
]

PARSER_NAME = "twfi-layout"

#: PyMuPDF span flag bit for a bold face.
_BOLD_FLAG = 1 << 4

#: Heading numbering used in Traditional Chinese filings, most specific first.
#: The level each pattern implies is fixed by convention, not inferred from size,
#: because numbering is a far stronger signal than typography.
_NUMBERING_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百]+章"), 1),
    (re.compile(r"^[壹貳參肆伍陸柒捌玖拾]+[、.]"), 1),
    (re.compile(r"^第[一二三四五六七八九十百]+節"), 2),
    (re.compile(r"^[一二三四五六七八九十]+[、]"), 2),
    (re.compile(r"^[（(][一二三四五六七八九十]+[)）]"), 3),
    (re.compile(r"^[（(]\d+[)）]"), 4),
)

#: Arabic numbering. A bare number followed by a space is deliberately NOT accepted:
#: on a real filing that matched every table row beginning with a figure
#: (``1 現金及約當現金 1,234``), which produced 23,677 "headings" in a 707-page report.
#: A dotted form may be followed by a space because ``1.2 明細`` is unambiguous.
_DOTTED_NUMBER = re.compile(r"^(\d{1,2}(?:\.\d{1,2})+)[.、\s]")
#: A single-level number, and the negative lookahead is the whole point of it.
#:
#: Without ``(?!\d)`` this matched the ``0.`` of ``0.00``, so **every decimal in every table
#: became a level-1 heading**. Measured before the fix: 1301-FY2023-AR had 1,083 distinct
#: top-level sections named things like ``0.0005%`` and ``0.0036,204,112``, and 2330-FY2023-AR
#: had 627. A filing has on the order of ten. The existing tabular guard did not catch it
#: because ``0.00`` is a single figure and one figure is allowed in a heading.
#:
#: The damage was not only fragmentation. F1's claim is that its chunks carry a section path,
#: and a path reading ``0.0036,204,112`` makes that claim hollow -- 6,515 sections, 83% of them
#: holding one chunk, and 29% of chunks under 50 characters all follow from this one pattern.
_SINGLE_NUMBER = re.compile(r"^(\d{1,2})[.、](?!\d)")

#: The level a bare ``1.`` implies. Matches ``(1)`` in the table above, because both are the
#: deepest customary level in these filings rather than the shallowest.
_ARABIC_LEVEL = 4

#: A line ending in sentence punctuation is prose, not a heading.
_SENTENCE_END = ("。", "；", ".", ";", "，", ",", "、")

#: A figure with thousands separators, three or more digits, or a decimal point.
_NUMBER_GROUP = re.compile(r"\d[\d,]{2,}|\d+\.\d+")

#: Two or more figures on one line means a table row. One is fine: a heading may
#: legitimately contain a year (``1.2 民國112年度概況``).
_MAX_FIGURES_IN_A_HEADING = 1


@dataclass(frozen=True, slots=True)
class LayoutConfig:
    """Thresholds for structure detection.

    Every value here is a knob, which means every value here may only be tuned on
    the development split (protocol 1.3). The defaults are the frozen ones.
    """

    heading_size_ratio: float = 1.15
    heading_max_chars: int = 80
    header_zone_ratio: float = 0.07
    footer_zone_ratio: float = 0.07
    repeat_threshold: float = 0.5
    min_pages_for_repeat: int = 3
    paragraph_gap_ratio: float = 1.8
    y_band: float = 3.0
    #: A font size carrying at least this share of the document's characters is body
    #: copy, not headings. Real filings use several body sizes -- 2317-FY2023 sets
    #: 209k characters at 10pt and another 106k at 12pt -- and treating only the
    #: single most common size as body promoted all 106k of those to headings.
    body_share_threshold: float = 0.03
    #: …but only once there is enough text for a share to mean anything. Without this
    #: floor, a three-page fixture's headings would each clear 3% and be misread as
    #: body copy.
    min_chars_for_body_size: int = 2000
    #: Headings deeper than this are collapsed. Ranking every distinct size produced
    #: level 14 on a real filing, which is meaningless as a section path.
    max_heading_level: int = 6

    def __post_init__(self) -> None:
        if self.heading_size_ratio <= 1.0:
            raise ValueError("heading_size_ratio must exceed 1.0 to distinguish headings")
        if not 0.0 < self.repeat_threshold <= 1.0:
            raise ValueError("repeat_threshold must be in (0, 1]")
        if not 0.0 < self.body_share_threshold < 1.0:
            raise ValueError("body_share_threshold must be in (0, 1)")
        if self.max_heading_level < 1:
            raise ValueError("max_heading_level must be at least 1")


@dataclass(frozen=True, slots=True)
class RawPage:
    """A page reduced to its lines, before any structural interpretation."""

    number: int
    width: float
    height: float
    lines: tuple[Line, ...] = ()


# --------------------------------------------------------------- pure helpers


def detect_numbering_level(text: str) -> int | None:
    """Return the heading level implied by a numbering prefix, if any.

    ``一、`` is level 2, ``（一）`` level 3, ``1.`` level 4, ``1.2.3`` level 3.
    Returns ``None`` when the text carries no recognised numbering.
    """
    stripped = text.strip()
    if not stripped:
        return None
    for pattern, level in _NUMBERING_PATTERNS:
        if pattern.match(stripped):
            return level
    dotted = _DOTTED_NUMBER.match(stripped)
    if dotted:
        return min(dotted.group(1).count(".") + 1, 6)
    if _SINGLE_NUMBER.match(stripped):
        # Level 4, not 1. The convention in these filings runs 壹/貳 -> 一/二 -> （一）/（二） ->
        # 1./2., so a bare arabic number is the *deepest* customary level, and ``(1)`` above
        # already maps there. Returning 1 made every numbered list item in the document a
        # top-level section: 1301-FY2023-AR reported 518 of them against the ten a filing
        # has, because each list restarts at 1. and each got its own root.
        return _ARABIC_LEVEL
    return None


def _strip_numbering(text: str) -> str:
    """Remove a leading numbering token so it is not counted as a figure."""
    stripped = text.strip()
    for pattern, _level in _NUMBERING_PATTERNS:
        match = pattern.match(stripped)
        if match:
            return stripped[match.end() :]
    for pattern in (_DOTTED_NUMBER, _SINGLE_NUMBER):
        match = pattern.match(stripped)
        if match:
            return stripped[match.end() :]
    return stripped


def looks_tabular(text: str) -> bool:
    """True when a line carries enough figures to be a table row rather than a heading.

    This is the guard that stopped statement rows from being promoted to headings.
    ``一、營業收入 2,894,308 2,161,736`` has the shape of a heading and the content of
    a table row; the figures are what give it away.

    The numbering prefix is stripped before counting, or ``1.2 民國112年度概況`` would
    look like two figures and lose its heading status.
    """
    return len(_NUMBER_GROUP.findall(_strip_numbering(text))) > _MAX_FIGURES_IN_A_HEADING


def _size_weights(pages: tuple[RawPage, ...]) -> Counter[float]:
    """Characters per rounded font size across the whole document."""
    weights: Counter[float] = Counter()
    for page in pages:
        for line in page.lines:
            text = line.text
            if text:
                weights[round(line.size, 1)] += len(text)
    return weights


def body_font_size(pages: tuple[RawPage, ...]) -> float:
    """The dominant body text size, weighted by character count.

    Character weighting matters: a filing has a handful of large headings and
    thousands of body characters, so an unweighted mode over lines would be skewed
    by short lines.
    """
    weights = _size_weights(pages)
    if not weights:
        return 0.0
    return max(weights.items(), key=lambda item: (item[1], -item[0]))[0]


def body_font_sizes(
    pages: tuple[RawPage, ...], config: LayoutConfig | None = None
) -> tuple[float, ...]:
    """Every font size that carries enough characters to be body copy.

    Real filings use more than one body size -- 2317-FY2023 sets 209k characters at
    10pt and another 106k at 12pt, in different chapters. Taking only the single most
    common size as "body" made every 12pt character a heading candidate, which is how
    that document produced 23,035 headings across 707 pages.

    Falls back to the single dominant size when the document is too short for a share
    to be meaningful, which keeps small fixtures behaving sensibly.
    """
    config = config or LayoutConfig()
    weights = _size_weights(pages)
    if not weights:
        return ()

    total = sum(weights.values())
    floor = max(config.min_chars_for_body_size, total * config.body_share_threshold)
    sizes = sorted(size for size, count in weights.items() if count >= floor)
    return tuple(sizes) if sizes else (body_font_size(pages),)


def _normalise(text: str) -> str:
    """Collapse whitespace and strip digits so page numbers still match."""
    return re.sub(r"\d+", "#", " ".join(text.split()))


def repeated_furniture(pages: tuple[RawPage, ...], config: LayoutConfig) -> set[str]:
    """Normalised text of lines that are page furniture rather than content.

    A running header or footer is identified by *repetition in the margin zone*,
    not by position alone: a real heading can sit near the top of a page, but it
    does not appear on most pages of the document.
    """
    if len(pages) < config.min_pages_for_repeat:
        return set()

    seen_on_pages: dict[str, set[int]] = {}
    for page in pages:
        header_limit = page.height * config.header_zone_ratio
        footer_limit = page.height * (1.0 - config.footer_zone_ratio)
        for line in page.lines:
            in_margin = line.bbox.y1 <= header_limit or line.bbox.y0 >= footer_limit
            if not in_margin:
                continue
            key = _normalise(line.text)
            if key:
                seen_on_pages.setdefault(key, set()).add(page.number)

    required = max(2, int(len(pages) * config.repeat_threshold))
    return {key for key, page_numbers in seen_on_pages.items() if len(page_numbers) >= required}


def reading_order(lines: tuple[Line, ...], y_band: float) -> tuple[Line, ...]:
    """Sort lines top-to-bottom, then left-to-right within a horizontal band.

    Banding stops sub-point vertical jitter from scrambling a row of cells that
    are visually side by side.
    """
    return tuple(
        sorted(lines, key=lambda line: (round(line.bbox.y0 / max(y_band, 0.1)), line.bbox.x0))
    )


def _could_be_heading(text: str, config: LayoutConfig) -> bool:
    """Shape checks that any heading must pass, whatever the other evidence says.

    Numbering is a strong signal but not an overriding one: a paragraph that merely
    *starts* with ``第二節`` or ``一、`` is still prose, and it gives itself away by
    running long and ending in sentence punctuation. Letting numbering bypass these
    checks turned body text into spurious headings, which then hijacked the section
    path for everything after it.

    The tabular check matters even more on real filings, which are mostly statements:
    a row like ``一、營業收入 2,894,308 2,161,736`` passes every other test.
    """
    return (
        bool(text)
        and len(text) <= config.heading_max_chars
        and not text.endswith(_SENTENCE_END)
        and not looks_tabular(text)
    )


def _is_heading(line: Line, *, dominant: float, ceiling: float, config: LayoutConfig) -> bool:
    """Typographic evidence for a heading.

    The size bar is the *largest* body size, not the most common one: a document
    whose chapters use 10pt and 12pt body copy must not treat the 12pt chapters as
    one enormous heading. Boldness is still judged against the dominant size, since
    a bold run at ordinary size is a recognisable heading style.
    """
    if not _could_be_heading(line.text, config):
        return False
    if ceiling <= 0:
        return line.bold
    if line.size >= ceiling * config.heading_size_ratio:
        return True
    return line.bold and line.size >= dominant


def _level_by_size(sizes: list[float], max_level: int) -> dict[float, int]:
    """Map heading sizes to levels: largest size is level 1, next is 2, and so on.

    Levels are capped: ranking every distinct size on a real filing produced level
    14, which is not a section path anyone can use.
    """
    return {
        size: min(index, max_level)
        for index, size in enumerate(sorted(set(sizes), reverse=True), start=1)
    }


def classify_pages(
    pages: tuple[RawPage, ...], doc_id: str, config: LayoutConfig | None = None
) -> ParsedDocument:
    """Turn raw lines into headings, paragraphs, furniture, and a section tree.

    Pure: no filesystem, no PDF library. This is where the candidate parser's
    behaviour actually lives, and therefore where it is tested.
    """
    config = config or LayoutConfig()
    dominant = body_font_size(pages)
    body_sizes = body_font_sizes(pages, config)
    ceiling = max(body_sizes) if body_sizes else 0.0
    furniture = repeated_furniture(pages, config)

    # First pass: which lines are headings, and at what size. Numbered headings are
    # excluded here because their level comes from the numbering, not from the size.
    heading_sizes: list[float] = []
    for page in pages:
        for line in page.lines:
            text = line.text
            if _normalise(text) in furniture or not _could_be_heading(text, config):
                continue
            if detect_numbering_level(text) is None and _is_heading(
                line, dominant=dominant, ceiling=ceiling, config=config
            ):
                heading_sizes.append(round(line.size, 1))
    size_levels = _level_by_size(heading_sizes, config.max_heading_level)

    # The section stack persists across pages, which is what makes a table that
    # continues onto the next page keep the section it belongs to.
    section_stack: list[tuple[int, str]] = []
    parsed_pages = [
        ParsedPage(
            number=page.number,
            width=page.width,
            height=page.height,
            blocks=_classify_page(
                page,
                furniture=furniture,
                dominant=dominant,
                ceiling=ceiling,
                size_levels=size_levels,
                section_stack=section_stack,
                config=config,
            ),
        )
        for page in pages
    ]
    return ParsedDocument(doc_id=doc_id, parser=PARSER_NAME, pages=tuple(parsed_pages))


def _paragraph_block(
    lines: list[Line], page_number: int, order: int, section_path: tuple[str, ...]
) -> Block | None:
    """Merge buffered lines into one paragraph block, or ``None`` if empty."""
    if not lines:
        return None
    text = " ".join(line.text for line in lines).strip()
    if not text:
        return None
    bbox = lines[0].bbox
    for line in lines[1:]:
        bbox = bbox.union(line.bbox)
    return Block(
        page=page_number,
        kind="paragraph",
        text=text,
        bbox=bbox,
        order=order,
        font_size=lines[0].size,
        section_path=section_path,
    )


def _classify_page(
    page: RawPage,
    *,
    furniture: set[str],
    dominant: float,
    ceiling: float,
    size_levels: dict[float, int],
    section_stack: list[tuple[int, str]],
    config: LayoutConfig,
) -> tuple[Block, ...]:
    """Classify one page's lines, mutating ``section_stack`` as headings are seen."""
    blocks: list[Block] = []
    pending: list[Line] = []
    order = 0

    for line in reading_order(page.lines, config.y_band):
        text = line.text
        if not text:
            continue

        if _normalise(text) in furniture:
            paragraph = _paragraph_block(
                pending, page.number, order, tuple(name for _level, name in section_stack)
            )
            if paragraph is not None:
                blocks.append(paragraph)
                order += 1
            pending = []
            blocks.append(
                Block(
                    page=page.number,
                    kind="header_footer",
                    text=text,
                    bbox=line.bbox,
                    order=order,
                    font_size=line.size,
                )
            )
            order += 1
            continue

        shaped_like_heading = _could_be_heading(text, config)
        numbering_level = detect_numbering_level(text) if shaped_like_heading else None
        is_heading = numbering_level is not None or _is_heading(
            line, dominant=dominant, ceiling=ceiling, config=config
        )
        if is_heading:
            paragraph = _paragraph_block(
                pending, page.number, order, tuple(name for _level, name in section_stack)
            )
            if paragraph is not None:
                blocks.append(paragraph)
                order += 1
            pending = []

            level = min(
                numbering_level or size_levels.get(round(line.size, 1), 1),
                config.max_heading_level,
            )
            while section_stack and section_stack[-1][0] >= level:
                section_stack.pop()
            section_stack.append((level, text))
            blocks.append(
                Block(
                    page=page.number,
                    kind="heading",
                    text=text,
                    bbox=line.bbox,
                    order=order,
                    font_size=line.size,
                    level=level,
                    section_path=tuple(name for _level, name in section_stack),
                )
            )
            order += 1
            continue

        if pending:
            gap = line.bbox.y0 - pending[-1].bbox.y1
            if gap > max(line.size, 1.0) * config.paragraph_gap_ratio:
                paragraph = _paragraph_block(
                    pending, page.number, order, tuple(name for _level, name in section_stack)
                )
                if paragraph is not None:
                    blocks.append(paragraph)
                    order += 1
                pending = []
        pending.append(line)

    paragraph = _paragraph_block(
        pending, page.number, order, tuple(name for _level, name in section_stack)
    )
    if paragraph is not None:
        blocks.append(paragraph)

    return tuple(blocks)


# ----------------------------------------------------------- PDF extraction


def _span_from_dict(payload: dict[str, Any]) -> Span | None:
    # Same normalisation as the baseline parser, for the same reason: 年 as U+F98E must
    # compare equal to 年 as U+5E74 on both sides of the comparison or on neither.
    text = normalise(str(payload.get("text", "")))
    if not text.strip():
        return None
    raw_bbox = payload.get("bbox")
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return None
    flags = int(payload.get("flags", 0) or 0)
    return Span(
        text=text,
        bbox=BBox.from_tuple((raw_bbox[0], raw_bbox[1], raw_bbox[2], raw_bbox[3])),
        size=float(payload.get("size", 0.0) or 0.0),
        font=str(payload.get("font", "")),
        bold=bool(flags & _BOLD_FLAG) or "bold" in str(payload.get("font", "")).lower(),
    )


def extract_raw_pages(pdf_path: Path) -> tuple[RawPage, ...]:
    """Read every page's lines and spans, with no structural interpretation.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    try:
        document = pymupdf.open(pdf_path)  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise ParsingError(f"cannot open {pdf_path} as a PDF: {exc}") from exc

    pages: list[RawPage] = []
    with document:
        for index in range(1, document.page_count + 1):
            page = document.load_page(index - 1)  # type: ignore[no-untyped-call]
            payload = page.get_text("dict")
            lines: list[Line] = []
            for raw_block in payload.get("blocks", []):
                if raw_block.get("type") != 0:  # 1 == image block, handled by figures.py
                    continue
                for raw_line in raw_block.get("lines", []):
                    spans = tuple(
                        span
                        for span in (
                            _span_from_dict(raw_span) for raw_span in raw_line.get("spans", [])
                        )
                        if span is not None
                    )
                    if not spans:
                        continue
                    bbox = spans[0].bbox
                    for span in spans[1:]:
                        bbox = bbox.union(span.bbox)
                    lines.append(Line(spans=spans, bbox=bbox))
            rect = page.rect
            pages.append(
                RawPage(
                    number=index,
                    width=float(rect.width),
                    height=float(rect.height),
                    lines=tuple(lines),
                )
            )
    return tuple(pages)


def parse_layout(pdf_path: Path, doc_id: str, config: LayoutConfig | None = None) -> ParsedDocument:
    """Parse a PDF into a structured document.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    return classify_pages(extract_raw_pages(pdf_path), doc_id, config)
