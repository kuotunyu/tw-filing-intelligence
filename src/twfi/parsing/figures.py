"""Figure and chart regions: where the chart route gets its crops.

The protocol is strict about charts: a caption may be indexed, but the answer has to
come from the original pixels (D-006). That makes the crop rectangle load-bearing --
a wrong crop means the VLM reads the wrong chart and the citation points somewhere
the number never appeared.

Regions are found two ways, both from data already in the PDF:

* **Raster images** -- PyMuPDF reports image blocks directly.
* **Vector drawings** -- a bar or line chart is dozens of small filled paths in a
  compact area. Clustering drawing rectangles and keeping dense clusters finds those
  without a layout model.

Both are then filtered on size and shape, because a filing is full of hairlines,
table rules, and logo fragments that are drawings but not charts. Captions are
attached by proximity (``圖一：``, ``附圖``) and are recorded on the figure so the
chart route can index them -- while :func:`crop_rect` remains the only thing an
answer may be read from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pymupdf

from twfi.errors import ParsingError
from twfi.parsing.types import BBox, Block

__all__ = [
    "FigureConfig",
    "Figure",
    "cluster_rects",
    "find_caption",
    "count_numeric_labels",
    "chart_candidates",
    "detect_figures",
    "figures_to_blocks",
    "render_crop",
]

#: A caption is a *label*, so it carries a number: 圖一、圖 2、附圖3、Figure 4.
#: Matching a bare 圖 prefix picked up prose instead -- on 2882-FY2024-AR it attached
#: 「圖」之規定，每年將進行滾動式盤點…」 to a chart. Requiring the number removes that
#: whole class of false positive.
_DIGIT = re.compile(r"\d")

_CAPTION_PATTERN = re.compile(
    r"^\s*(?:附?圖(?:表)?|Figure|Fig\.?|Chart|Exhibit)\s*[一二三四五六七八九十百\d]+\s*[：:.、\-\s]"
)

#: Extracted captions turn out to be rare in this corpus: 0, 0 and 1 across three
#: annual reports carrying 122, 457 and 181 detected figures. That is a finding rather
#: than a bug -- these filings label charts inconsistently or not at all -- and it is
#: why the chart route generates captions with the VLM (D-006) instead of relying on
#: extraction. Anything found here is a bonus for retrieval, never a value source.
CAPTION_SCARCITY_NOTE = "extracted captions are rare in this corpus; the chart route generates them"


@dataclass(frozen=True, slots=True)
class FigureConfig:
    """Thresholds for figure detection. Dev-split tunable only (protocol 1.3)."""

    #: A chart occupies real space. Below this fraction of the page it is decoration.
    min_area_ratio: float = 0.01
    #: Above this it is a full-page background, not a figure.
    max_area_ratio: float = 0.85
    #: A cluster needs this many drawing paths to be a chart rather than a rule.
    min_paths: int = 8
    #: Drawings within this distance (points) join the same cluster.
    cluster_gap: float = 24.0
    #: Extremely elongated regions are table rules or borders.
    max_aspect_ratio: float = 12.0
    #: A caption within this distance below (or above) a region is attached to it.
    caption_distance: float = 60.0
    #: Rendering resolution for crops. Protocol 2.5 fixes this at 200 dpi.
    crop_dpi: int = 200
    #: Longest crop edge, to bound VLM image tokens. Protocol 2.5.
    crop_max_edge: int = 1024
    #: How far outside a region an axis label may sit and still belong to it.
    label_margin: float = 14.0
    #: Numeric labels required before a region counts as a *chart* rather than
    #: decoration. An annual report's front section is full of vector artwork; a chart
    #: is distinguished by carrying axis ticks and data labels.
    min_numeric_labels: int = 3
    #: How much of a figure region must sit inside a detected table before the region
    #: is treated as that table rather than as a chart.
    table_overlap_ratio: float = 0.5

    def __post_init__(self) -> None:
        if not 0.0 < self.min_area_ratio < self.max_area_ratio <= 1.0:
            raise ValueError("area ratios must satisfy 0 < min < max <= 1")
        if self.min_paths < 1:
            raise ValueError("min_paths must be at least 1")


@dataclass(frozen=True, slots=True)
class Figure:
    """A figure region, its caption, and how it was found."""

    page: int
    bbox: BBox
    kind: str  # "image" | "vector"
    caption: str = ""
    path_count: int = 0
    numeric_labels: int = 0

    def has_labels(self, config: FigureConfig | None = None) -> bool:
        """Whether anything numeric sits inside or beside this region.

        Cover artwork, dividers, and logos have none, which is what this rules out.
        It does *not* rule out tables -- see :func:`chart_candidates`.
        """
        config = config or FigureConfig()
        return self.numeric_labels >= config.min_numeric_labels

    @property
    def crop_ref(self) -> str:
        """A citable reference to the crop, e.g. ``p214:crop:70,200,400,380``."""
        box = self.bbox
        return f"p{self.page}:crop:{box.x0:.0f},{box.y0:.0f},{box.x1:.0f},{box.y1:.0f}"

    def index_text(self) -> str:
        """What goes into the retrieval index.

        The caption is included here and only here. Protocol 2.4 requires the final
        numeric answer to come from the crop pixels, so the caption must never be the
        provenance of a value.
        """
        label = self.caption or f"（未命名圖表，第 {self.page} 頁）"
        return f"{label}\n[{self.kind} figure at {self.crop_ref}]"


def cluster_rects(
    rects: list[BBox], gap: float, page_area: float, config: FigureConfig
) -> list[tuple[BBox, int]]:
    """Group nearby drawing rectangles and return the clusters worth keeping.

    Single-linkage on an expanded box: two rectangles join when their neighbourhoods
    touch. Returns ``(bounding box, path count)`` for clusters that pass the size,
    shape, and density filters.
    """
    remaining = list(rects)
    clusters: list[tuple[BBox, int]] = []

    while remaining:
        box = remaining.pop()
        members = 1
        changed = True
        while changed:
            changed = False
            grown = box.expanded(gap)
            for candidate in list(remaining):
                if grown.intersection_area(candidate.expanded(gap)) > 0:
                    box = box.union(candidate)
                    remaining.remove(candidate)
                    members += 1
                    changed = True
                    grown = box.expanded(gap)
        clusters.append((box, members))

    return [
        (box, members)
        for box, members in clusters
        if members >= config.min_paths and _passes_shape(box, page_area, config)
    ]


def _passes_shape(box: BBox, page_area: float, config: FigureConfig) -> bool:
    if page_area <= 0 or box.width <= 0 or box.height <= 0:
        return False
    ratio = box.area / page_area
    if not config.min_area_ratio <= ratio <= config.max_area_ratio:
        return False
    aspect = max(box.width / box.height, box.height / box.width)
    return aspect <= config.max_aspect_ratio


def find_caption(region: BBox, candidates: list[tuple[BBox, str]], config: FigureConfig) -> str:
    """Return the caption belonging to ``region``, or ``""``.

    Preference goes to a caption below the figure, which is the convention in these
    filings, then to one above. Distance is measured vertically, and horizontal
    overlap is required so a caption belonging to a neighbouring column is not stolen.
    """
    best: tuple[float, str] = (config.caption_distance + 1.0, "")
    for box, text in candidates:
        if _CAPTION_PATTERN.match(text) is None:
            continue
        horizontal_overlap = min(region.x1, box.x1) - max(region.x0, box.x0)
        if horizontal_overlap <= 0:
            continue
        below = box.y0 - region.y1
        above = region.y0 - box.y1
        distance = below if below >= 0 else (above if above >= 0 else 0.0)
        if distance <= config.caption_distance and distance < best[0]:
            best = (distance, text.strip())
    return best[1]


def chart_candidates(
    figures: tuple[Figure, ...],
    table_regions: list[tuple[int, BBox]],
    config: FigureConfig | None = None,
) -> tuple[Figure, ...]:
    """Narrow detected figures down to regions the chart route should read.

    Two exclusions, in the order they matter:

    1. **Ruled tables.** Vector clustering finds table grids, not just charts -- a
       bordered statement is hundreds of small rectangles in a compact area with
       numbers all over it, which is exactly the shape a bar chart has. The strongest
       available discriminator is the table extractor's own output: if pdfplumber
       already read a table there, the region is a table.
    2. **Artwork.** A logo or cover graphic has no numeric labels near it.

    Ordering matters because a ruled table scores *higher* than a real chart on every
    density measure; filtering on labels alone kept the tables and dropped the charts.
    """
    config = config or FigureConfig()
    kept: list[Figure] = []
    for figure in figures:
        area = figure.bbox.area
        overlaps_table = any(
            page == figure.page
            and area > 0
            and box.intersection_area(figure.bbox) / area >= config.table_overlap_ratio
            for page, box in table_regions
        )
        if overlaps_table or not figure.has_labels(config):
            continue
        kept.append(figure)
    return tuple(kept)


def count_numeric_labels(
    region: BBox, candidates: list[tuple[BBox, str]], config: FigureConfig
) -> int:
    """Count text boxes carrying digits inside or immediately around ``region``."""
    grown = region.expanded(config.label_margin)
    return sum(
        1 for box, text in candidates if _DIGIT.search(text) and grown.intersection_area(box) > 0
    )


def detect_figures(pdf_path: Path, config: FigureConfig | None = None) -> tuple[Figure, ...]:
    """Find every figure region in a PDF, with captions attached.

    Raises:
        ParsingError: If the file cannot be opened as a PDF.
    """
    config = config or FigureConfig()
    try:
        document = pymupdf.open(pdf_path)  # type: ignore[no-untyped-call]
    except Exception as exc:
        raise ParsingError(f"cannot open {pdf_path} for figure detection: {exc}") from exc

    figures: list[Figure] = []
    with document:
        for index in range(document.page_count):
            page = document.load_page(index)  # type: ignore[no-untyped-call]
            number = index + 1
            rect = page.rect
            page_area = float(rect.width) * float(rect.height)

            captions = _caption_candidates(page)

            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 1:  # 1 == image
                    continue
                box = _bbox_of(block.get("bbox"))
                if box is None or not _passes_shape(box, page_area, config):
                    continue
                figures.append(
                    Figure(
                        page=number,
                        bbox=box,
                        kind="image",
                        caption=find_caption(box, captions, config),
                        numeric_labels=count_numeric_labels(box, captions, config),
                    )
                )

            rects = [
                box
                for box in (_bbox_of(drawing.get("rect")) for drawing in page.get_drawings())
                if box is not None
            ]
            for box, members in cluster_rects(rects, config.cluster_gap, page_area, config):
                figures.append(
                    Figure(
                        page=number,
                        bbox=box,
                        kind="vector",
                        caption=find_caption(box, captions, config),
                        path_count=members,
                        numeric_labels=count_numeric_labels(box, captions, config),
                    )
                )

    return tuple(figures)


def _bbox_of(raw: Any) -> BBox | None:
    """Convert a PyMuPDF rect or 4-tuple to a BBox, or ``None`` if degenerate."""
    try:
        values = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, IndexError, ValueError):
        return None
    if values[2] < values[0] or values[3] < values[1]:
        return None
    return BBox(*values)


def _caption_candidates(page: Any) -> list[tuple[BBox, str]]:
    candidates: list[tuple[BBox, str]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(str(span.get("text", "")) for span in line.get("spans", []))
            box = _bbox_of(line.get("bbox"))
            if box is not None and text.strip():
                candidates.append((box, text))
    return candidates


def figures_to_blocks(figures: tuple[Figure, ...]) -> tuple[Block, ...]:
    """Render figures as atomic blocks, carrying only their caption text."""
    return tuple(
        Block(
            page=figure.page,
            kind="figure",
            text=figure.index_text(),
            bbox=figure.bbox,
            order=index,
        )
        for index, figure in enumerate(figures)
    )


def render_crop(
    pdf_path: Path, figure: Figure, destination: Path, config: FigureConfig | None = None
) -> Path:
    """Render one figure region to a PNG at the configured dpi.

    This is the image the chart route reads values from. The caption never is.

    Raises:
        ParsingError: If the page cannot be rendered.
    """
    config = config or FigureConfig()
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with pymupdf.open(pdf_path) as document:  # type: ignore[no-untyped-call]
            page = document.load_page(figure.page - 1)
            clip = pymupdf.Rect(*figure.bbox.as_tuple())  # type: ignore[no-untyped-call]
            pixmap = page.get_pixmap(dpi=config.crop_dpi, clip=clip)
            longest = max(pixmap.width, pixmap.height)
            if longest > config.crop_max_edge:
                scale = config.crop_max_edge / longest
                pixmap = page.get_pixmap(dpi=max(1, int(config.crop_dpi * scale)), clip=clip)
            pixmap.save(destination)
    except Exception as exc:
        raise ParsingError(f"cannot render {figure.crop_ref} from {pdf_path}: {exc}") from exc
    return destination
