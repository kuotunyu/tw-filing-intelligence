"""Render cited pages to PNG so an annotator reads pixels, not extracted text.

    uv run python scripts/render_pages.py 2330-FY2024-FS 55 56
    uv run python scripts/render_pages.py --from-probes

Reading a figure off this repository's own text extraction would make gold answers
correlated with the system under test: a misread digit becomes a wrong gold answer that
the candidate, running the same extractor, reproduces and is scored correct for (D-016).
A rendered page is the same pixels a PDF viewer shows, so it bypasses the text layer
entirely and is a legitimate reading surface.

It is also the *only* way to read some pages. 2330-FY2024-FS has no text layer on
pp.7-15 -- its four primary statements -- so nothing but rendering can reach them.

The printed page label appears in the image, which matters because the page numbers in
the worklist are PDF positions. A filing with a cover and a table of contents puts
printed page 1 several PDF pages in, and citing the wrong one would put a real figure
against the wrong page.

Output goes to `results/runs/pages/` and is never committed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import pymupdf
import typer

from twfi.errors import ParsingError
from twfi.io.manifest import load_acquisition_lock
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

#: High enough to read a statement's digits without zooming.
RENDER_DPI = 200


@app.command()
def main(
    doc_id: Annotated[str | None, typer.Argument(help="Document id, e.g. 2330-FY2024-FS.")] = None,
    pages: Annotated[list[int] | None, typer.Argument(help="PDF page numbers, 1-based.")] = None,
    from_probes: Annotated[
        bool, typer.Option("--from-probes", help="Render every page cited in probes.jsonl.")
    ] = False,
    dpi: Annotated[int, typer.Option(help="Render resolution.")] = RENDER_DPI,
) -> None:
    """Render pages to PNG for annotation."""
    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    target_dir = paths.runs / "pages"

    requests: dict[str, list[int]] = {}
    if from_probes:
        requests = _pages_from_probes(paths.locked_probes)
        if not requests:
            typer.echo(f"no page citations found in {paths.locked_probes}")
            raise typer.Exit(code=2)
    elif doc_id and pages:
        requests = {doc_id: list(pages)}
    else:
        typer.echo("give a doc_id and page numbers, or --from-probes")
        raise typer.Exit(code=2)

    written = 0
    for document, page_numbers in sorted(requests.items()):
        record = lock.get(document)
        if record is None or not record.local_path(paths.root).is_file():
            typer.echo(f"{document}: not acquired")
            continue
        pdf_path = record.local_path(paths.root)
        for page in sorted(set(page_numbers)):
            try:
                out = _render(pdf_path, document, page, target_dir, dpi=dpi)
            except (ParsingError, ValueError) as exc:
                typer.echo(f"{document} p{page}: {exc}")
                continue
            typer.echo(f"{document} p{page:>4}  ->  {out.relative_to(paths.root)}")
            written += 1

    typer.echo("")
    typer.echo(f"rendered {written} page(s) into {target_dir.relative_to(paths.root)}")
    typer.echo("")
    typer.echo("Read the figure off the image, not off any extracted text. The page number")
    typer.echo("in the filename is the PDF position; check it against the printed label in")
    typer.echo("the image before writing a citation.")


def _render(pdf_path: Path, doc_id: str, page: int, target_dir: Path, *, dpi: int) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{doc_id}-p{page:04d}.png"
    try:
        with pymupdf.open(pdf_path) as document:  # type: ignore[no-untyped-call]
            if not 1 <= page <= document.page_count:
                raise ValueError(f"page {page} outside 1-{document.page_count}")
            pixmap = document.load_page(page - 1).get_pixmap(dpi=dpi)
            pixmap.save(destination)
    except pymupdf.FileDataError as exc:  # pragma: no cover - corrupt PDF
        raise ParsingError(f"cannot render {pdf_path.name}: {exc}") from exc
    return destination


def _pages_from_probes(probes: Path) -> dict[str, list[int]]:
    """Collect (document, pages) from a probes file, filled in or not.

    Reads the raw JSON rather than going through ``load_gold``, because the whole point
    is to render pages for a file that does not validate yet.
    """
    if not probes.is_file():
        return {}
    found: dict[str, list[int]] = {}
    for line in probes.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        for document in payload.get("source_document", ()):
            found.setdefault(str(document), []).extend(
                int(page) for page in payload.get("page_numbers", ())
            )
    return found


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
