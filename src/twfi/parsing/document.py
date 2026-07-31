"""Assembling one document out of three extractors.

Layout, tables, and figures each read the same pages independently, so their output
overlaps: the lines inside a statement table are also paragraphs, and the tick labels
around a chart are also text. Emitting all of it would put the same figures into the
index twice -- once as prose the parser did not understand and once as a structured
table -- and retrieval would then rank the worse copy about as often as the better
one.

So assembly resolves the overlap in favour of the structured extractor, gives tables
and figures the section they sit under, and renumbers everything into a single reading
order per page. What comes out is one :class:`~twfi.parsing.types.ParsedDocument` that
the chunker can treat uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from twfi.parsing.figures import (
    Figure,
    FigureConfig,
    chart_candidates,
    detect_figures,
    figures_to_blocks,
)
from twfi.parsing.layout import LayoutConfig, classify_pages, extract_raw_pages
from twfi.parsing.tables import Table, TableConfig, extract_tables, tables_to_blocks
from twfi.parsing.types import Block, ParsedDocument, ParsedPage

__all__ = ["DocumentConfig", "AssembledDocument", "assemble", "parse_document"]

PARSER_NAME = "twfi-full"

#: Structured blocks take precedence over the prose they contain.
_STRUCTURED_KINDS = frozenset({"table", "figure"})


@dataclass(frozen=True, slots=True)
class DocumentConfig:
    """Settings for every stage, plus how the stages' outputs are reconciled."""

    layout: LayoutConfig = field(default_factory=LayoutConfig)
    tables: TableConfig = field(default_factory=TableConfig)
    figures: FigureConfig = field(default_factory=FigureConfig)
    #: A paragraph this much inside a table or figure is dropped in its favour.
    #: Measured against the paragraph's own area, so a small caption inside a large
    #: chart is removed while a long paragraph merely clipped by one is kept.
    overlap_ratio: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.overlap_ratio <= 1.0:
            raise ValueError("overlap_ratio must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class AssembledDocument:
    """The assembled document plus what each extractor contributed."""

    document: ParsedDocument
    tables: tuple[Table, ...] = ()
    figures: tuple[Figure, ...] = ()
    dropped_overlapping_blocks: int = 0
    #: Regions detected but excluded from the chart route: ruled tables and artwork.
    #: Reported rather than silently discarded -- a bounded pipeline that does not say
    #: what it bounded reads as full coverage.
    discarded_figures: int = 0

    def stats(self) -> dict[str, int]:
        counts = dict(self.document.stats())
        counts["extracted_tables"] = len(self.tables)
        counts["chart_candidates"] = len(self.figures)
        counts["discarded_figures"] = self.discarded_figures
        counts["dropped_overlapping_blocks"] = self.dropped_overlapping_blocks
        return counts


def _is_covered(block: Block, structured: list[Block], ratio: float) -> bool:
    """True when a prose block sits mostly inside a table or figure region."""
    area = block.bbox.area
    if area <= 0:
        return False
    return any(
        other.page == block.page and other.bbox.intersection_area(block.bbox) / area >= ratio
        for other in structured
    )


def _assign_sections(blocks: list[Block]) -> list[Block]:
    """Give structured blocks the section that was open where they appear.

    A table chunk with no section path loses the context that says which statement it
    belongs to, which is exactly what the structure-aware parser exists to preserve.
    """
    assigned: list[Block] = []
    current: tuple[str, ...] = ()
    for block in blocks:
        if block.section_path:
            current = block.section_path
            assigned.append(block)
            continue
        if block.kind in _STRUCTURED_KINDS and current:
            assigned.append(
                Block(
                    page=block.page,
                    kind=block.kind,
                    text=block.text,
                    bbox=block.bbox,
                    order=block.order,
                    font_size=block.font_size,
                    level=block.level,
                    section_path=current,
                )
            )
            continue
        assigned.append(block)
    return assigned


def assemble(
    layout_document: ParsedDocument,
    tables: tuple[Table, ...],
    figures: tuple[Figure, ...],
    config: DocumentConfig | None = None,
) -> AssembledDocument:
    """Merge the three extractors' blocks into one document.

    Figures are narrowed to chart candidates first. Vector clustering finds table
    grids as readily as charts -- across eight real filings it produced 1,744 regions,
    of which 1,241 were ruled tables or artwork -- and indexing those would fill the
    chart route with material it cannot answer from.

    Pure: takes already-extracted output, so the reconciliation rules are tested
    without touching a PDF.
    """
    config = config or DocumentConfig()
    table_regions = [(table.page, table.bbox) for table in tables]
    charts = chart_candidates(figures, table_regions, config.figures)

    table_blocks = list(tables_to_blocks(tables))
    figure_blocks = list(figures_to_blocks(charts))
    structured = table_blocks + figure_blocks

    # Pages come from the union, not from layout alone. A page whose only content is a
    # ruled table has no text lines, so layout may not describe it -- and iterating
    # layout's pages would then drop that table without saying so.
    known = {page.number: page for page in layout_document.pages}
    for block in structured:
        if block.page not in known:
            known[block.page] = ParsedPage(number=block.page, width=0.0, height=0.0)

    dropped = 0
    pages: list[ParsedPage] = []
    for number in sorted(known):
        page = known[number]
        kept: list[Block] = []
        for block in page.blocks:
            if block.kind == "paragraph" and _is_covered(block, structured, config.overlap_ratio):
                dropped += 1
                continue
            kept.append(block)

        kept.extend(item for item in structured if item.page == page.number)
        kept.sort(key=lambda item: (item.bbox.y0, item.bbox.x0))
        kept = _assign_sections(kept)
        pages.append(
            ParsedPage(
                number=page.number,
                width=page.width,
                height=page.height,
                blocks=tuple(
                    Block(
                        page=item.page,
                        kind=item.kind,
                        text=item.text,
                        bbox=item.bbox,
                        order=index,
                        font_size=item.font_size,
                        level=item.level,
                        section_path=item.section_path,
                    )
                    for index, item in enumerate(kept)
                ),
            )
        )

    return AssembledDocument(
        document=ParsedDocument(
            doc_id=layout_document.doc_id, parser=PARSER_NAME, pages=tuple(pages)
        ),
        tables=tables,
        figures=charts,
        dropped_overlapping_blocks=dropped,
        discarded_figures=len(figures) - len(charts),
    )


def parse_document(
    pdf_path: Path,
    doc_id: str,
    config: DocumentConfig | None = None,
    *,
    with_tables: bool = True,
    with_figures: bool = True,
) -> AssembledDocument:
    """Run every extractor over a PDF and assemble the result.

    ``with_tables`` and ``with_figures`` exist because table extraction costs about
    0.16 s/page and figure detection about 0.02 s/page; a caller that only needs text
    structure should not pay for both.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    config = config or DocumentConfig()
    layout_document = classify_pages(extract_raw_pages(pdf_path), doc_id, config.layout)
    tables = extract_tables(pdf_path, config.tables) if with_tables else ()
    figures = detect_figures(pdf_path, config.figures) if with_figures else ()
    return assemble(layout_document, tables, figures, config)
