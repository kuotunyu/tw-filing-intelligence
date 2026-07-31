"""Count how many genuine charts these filings contain, and how many the detector claims.

    uv run python scripts/measure_chart_prevalence.py

D-014 narrowed 1,744 figure candidates to 503 and reported the reduction as an
improvement. It never measured whether the survivors were charts. Five of five inspected
turned out to be tables (D-020), so this measures the thing that was skipped.

The discriminator D-014 lacked is geometric, and `detect_figures` was throwing it away:
it keeps only each drawing's bounding rectangle and discards `items`, the path commands.
A ruled table is built from axis-aligned rectangles and axis-aligned lines. A bar chart is
too -- but a line chart, a pie, an area chart or any trend line needs a diagonal segment or
a Bézier curve, and a table never does. So a region containing no diagonal and no curve is
either a table or a bar chart, while a region containing them is not a table.

That is a one-sided test, and it is reported as one: `no_curve_or_diagonal` counts regions
this cannot distinguish, rather than pretending they are charts.

Writes `results/runs/chart_prevalence.json`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import pymupdf
import typer

from twfi.errors import ParsingError
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.figures import chart_candidates, detect_figures
from twfi.parsing.tables import extract_tables
from twfi.parsing.types import BBox
from twfi.paths import repo_paths
from twfi.protocol import USABLE_DOCUMENTS

app = typer.Typer(add_completion=False, help=__doc__)

#: A segment counts as diagonal only when it departs from the axes by more than this many
#: points in both directions. Hairline skew in a ruled border is not a diagonal.
AXIS_TOLERANCE = 1.0


@app.command()
def main(
    render: Annotated[
        int, typer.Option(help="Render this many non-axis-aligned regions per document.")
    ] = 2,
) -> None:
    """Measure chart prevalence and the candidate set's precision."""
    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    target = paths.runs / "pages"
    target.mkdir(parents=True, exist_ok=True)

    report: list[dict[str, Any]] = []
    for document in USABLE_DOCUMENTS:
        acquired = lock.get(document.doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            continue
        pdf_path = acquired.local_path(paths.root)
        try:
            tables = extract_tables(pdf_path)
            candidates = chart_candidates(
                detect_figures(pdf_path), [(t.page, t.bbox) for t in tables]
            )
        except ParsingError as exc:
            typer.echo(f"{document.doc_id}: {exc}")
            continue

        shapes = _non_axis_shapes(pdf_path)
        with_geometry = [
            candidate
            for candidate in candidates
            if _contains_any(candidate.bbox, candidate.page, shapes)
        ]
        entry = {
            "doc_id": document.doc_id,
            "candidates": len(candidates),
            "with_curve_or_diagonal": len(with_geometry),
            "no_curve_or_diagonal": len(candidates) - len(with_geometry),
            "page_level_non_axis_shapes": sum(len(v) for v in shapes.values()),
            "pages_with_non_axis_shapes": len(shapes),
            "candidate_pages_with_geometry": sorted({c.page for c in with_geometry})[:20],
        }
        report.append(entry)
        typer.echo(
            f"{document.doc_id:<18} candidates {entry['candidates']:>4}  "
            f"with curve/diagonal {entry['with_curve_or_diagonal']:>4}  "
            f"pages carrying such shapes {entry['pages_with_non_axis_shapes']:>4}"
        )

        for candidate in sorted(with_geometry, key=lambda c: -c.numeric_labels)[:render]:
            out = (
                target / f"GEOM-{document.doc_id}-p{candidate.page}-L{candidate.numeric_labels}.png"
            )
            _render(pdf_path, candidate.page, candidate.bbox, out)
            typer.echo(f"    rendered {out.name}")

    total = sum(e["candidates"] for e in report)
    geometric = sum(e["with_curve_or_diagonal"] for e in report)
    typer.echo("")
    typer.echo(f"candidates across usable documents : {total}")
    typer.echo(f"of those, containing a curve or diagonal: {geometric}")
    typer.echo("")
    typer.echo("A region with no curve and no diagonal is a table or a bar chart -- this test")
    typer.echo("cannot tell those apart, and does not claim to.")

    destination = paths.runs / "chart_prevalence.json"
    destination.write_text(
        json.dumps(
            {"documents": report, "candidates": total, "with_geometry": geometric},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")


def _non_axis_shapes(pdf_path: Path) -> dict[int, list[BBox]]:
    """Bounding boxes of drawings that contain a curve or a diagonal segment.

    Reads `items`, which `detect_figures` discards. Each item is a tuple whose first
    element is the command: "c" a Bézier curve, "l" a line, "re" a rectangle, "qu" a quad.
    """
    found: dict[int, list[BBox]] = {}
    with pymupdf.open(pdf_path) as document:  # type: ignore[no-untyped-call]
        for index in range(document.page_count):
            page = document.load_page(index)
            boxes: list[BBox] = []
            for drawing in page.get_drawings():
                if not _has_curve_or_diagonal(drawing.get("items", ())):
                    continue
                rect = drawing.get("rect")
                if rect is None:
                    continue
                box = BBox(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))
                if box.x1 > box.x0 and box.y1 > box.y0:
                    boxes.append(box)
            if boxes:
                found[index + 1] = boxes
    return found


def _has_curve_or_diagonal(items: Any) -> bool:
    for item in items:
        if not item:
            continue
        command = item[0]
        if command == "c":
            return True
        if command == "l" and len(item) >= 3:
            start, end = item[1], item[2]
            if (
                abs(float(start.x) - float(end.x)) > AXIS_TOLERANCE
                and abs(float(start.y) - float(end.y)) > AXIS_TOLERANCE
            ):
                return True
    return False


def _contains_any(region: BBox, page: int, shapes: dict[int, list[BBox]]) -> bool:
    for box in shapes.get(page, ()):
        if (
            box.x0 >= region.x0 - AXIS_TOLERANCE
            and box.y0 >= region.y0 - AXIS_TOLERANCE
            and box.x1 <= region.x1 + AXIS_TOLERANCE
            and box.y1 <= region.y1 + AXIS_TOLERANCE
        ):
            return True
    return False


def _render(pdf_path: Path, page: int, box: BBox, destination: Path) -> None:
    with pymupdf.open(pdf_path) as document:  # type: ignore[no-untyped-call]
        clip = pymupdf.Rect(box.x0, box.y0, box.x1, box.y1)  # type: ignore[no-untyped-call]
        document.load_page(page - 1).get_pixmap(dpi=200, clip=clip).save(destination)


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
