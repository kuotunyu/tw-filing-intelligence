"""The document model shared by every stage of the pipeline.

Retrieval, generation, citation checking, and evaluation all speak this
vocabulary, and the reason it carries page numbers and bounding boxes everywhere
is the citation contract: an answer is only acceptable if its evidence resolves
to a real page, table cell, crop, or SQL row. A model that could not represent
"where this came from" would make that contract unenforceable.

Everything here is frozen and hashable so parsed output can be cached and
compared without worrying about accidental mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Self, get_args

__all__ = [
    "BBox",
    "Span",
    "Line",
    "BlockKind",
    "Block",
    "ParsedPage",
    "ParsedDocument",
    "Chunk",
    "PageRef",
]

#: What a block is, as far as downstream routing cares.
BlockKind = Literal["heading", "paragraph", "table", "figure", "caption", "header_footer"]


@dataclass(frozen=True, slots=True, order=True)
class BBox:
    """A PDF-space rectangle, origin top-left, in points."""

    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"degenerate bbox: {self}")

    @classmethod
    def from_tuple(cls, values: tuple[float, float, float, float]) -> Self:
        return cls(float(values[0]), float(values[1]), float(values[2]), float(values[3]))

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def center_y(self) -> float:
        return (self.y0 + self.y1) / 2

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)

    def union(self, other: BBox) -> BBox:
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersection_area(self, other: BBox) -> float:
        dx = min(self.x1, other.x1) - max(self.x0, other.x0)
        dy = min(self.y1, other.y1) - max(self.y0, other.y0)
        return dx * dy if dx > 0 and dy > 0 else 0.0

    def iou(self, other: BBox) -> float:
        """Intersection over union. The citation gate uses ``iou >= 0.3``."""
        overlap = self.intersection_area(other)
        if overlap == 0.0:
            return 0.0
        return overlap / (self.area + other.area - overlap)

    def expanded(self, margin: float) -> BBox:
        """Grow by ``margin`` on every side, clamped so the box stays valid."""
        return BBox(self.x0 - margin, self.y0 - margin, self.x1 + margin, self.y1 + margin)


@dataclass(frozen=True, slots=True)
class Span:
    """A run of characters sharing one font and size."""

    text: str
    bbox: BBox
    size: float
    font: str = ""
    bold: bool = False


@dataclass(frozen=True, slots=True)
class Line:
    """One visual line: the unit heading detection reasons about."""

    spans: tuple[Span, ...]
    bbox: BBox

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans).strip()

    @property
    def size(self) -> float:
        """The dominant size, weighted by how many characters use it."""
        if not self.spans:
            return 0.0
        weights: dict[float, int] = {}
        for span in self.spans:
            weights[round(span.size, 1)] = weights.get(round(span.size, 1), 0) + len(span.text)
        return max(weights.items(), key=lambda item: (item[1], item[0]))[0]

    @property
    def bold(self) -> bool:
        """True when most characters on the line are bold."""
        if not self.spans:
            return False
        bold_chars = sum(len(span.text) for span in self.spans if span.bold)
        total = sum(len(span.text) for span in self.spans)
        return total > 0 and bold_chars * 2 >= total


@dataclass(frozen=True, slots=True)
class Block:
    """A parsed region of one page, with everything a citation needs."""

    page: int
    kind: BlockKind
    text: str
    bbox: BBox
    order: int
    font_size: float = 0.0
    level: int | None = None
    section_path: tuple[str, ...] = ()

    @property
    def is_content(self) -> bool:
        """False for repeated page furniture, which never carries an answer."""
        return self.kind != "header_footer"


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page's blocks in reading order."""

    number: int
    width: float
    height: float
    blocks: tuple[Block, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks if block.is_content)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """A parsed filing, tagged with which parser produced it.

    ``parser`` is part of the identity on purpose: the study compares two parsers,
    so an artifact that does not say which one made it is not usable evidence.
    """

    doc_id: str
    parser: str
    pages: tuple[ParsedPage, ...] = ()

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def blocks(self) -> tuple[Block, ...]:
        return tuple(block for page in self.pages for block in page.blocks)

    def content_blocks(self) -> tuple[Block, ...]:
        return tuple(block for block in self.blocks if block.is_content)

    def blocks_of_kind(self, kind: BlockKind) -> tuple[Block, ...]:
        return tuple(block for block in self.blocks if block.kind == kind)

    def page(self, number: int) -> ParsedPage:
        """Return one page by its 1-based number.

        Raises:
            KeyError: If the document has no such page.
        """
        for page in self.pages:
            if page.number == number:
                return page
        raise KeyError(number)

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def stats(self) -> dict[str, int]:
        """Counts used for the parse-statistics artifact."""
        counts = {
            "pages": self.page_count,
            "blocks": len(self.blocks),
            "content_blocks": len(self.content_blocks()),
            "characters": len(self.text),
        }
        for kind in get_args(BlockKind):
            counts[kind] = len(self.blocks_of_kind(kind))
        return counts


@dataclass(frozen=True, slots=True)
class PageRef:
    """A page plus the region on it that a chunk actually covers."""

    page: int
    bbox: BBox


@dataclass(frozen=True, slots=True)
class Chunk:
    """A retrievable unit that still knows where it came from."""

    chunk_id: str
    doc_id: str
    text: str
    refs: tuple[PageRef, ...] = field(default_factory=tuple)
    section_path: tuple[str, ...] = ()
    kinds: tuple[BlockKind, ...] = ()
    parser: str = ""

    @property
    def pages(self) -> tuple[int, ...]:
        """Every page this chunk touches, ascending and deduplicated."""
        return tuple(sorted({ref.page for ref in self.refs}))

    @property
    def spans_pages(self) -> bool:
        """True for chunks that cross a page boundary (the cross-page metric cares)."""
        return len(self.pages) > 1

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def heading(self) -> str:
        return self.section_path[-1] if self.section_path else ""
