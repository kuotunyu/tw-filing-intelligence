"""Measure which question types each filing can actually source.

    uv run python scripts/check_question_sources.py

Runs the table and figure extractors over every declared document and derives, per
document, which question types may draw on it. Writes
`results/runs/question_sources.json`.

This exists because a single `usable` flag was too coarse to annotate against:
`2330-FY2024-FS` is 91% readable and holds the FY2024 revenue figure in its notes, while
its four primary statements have no text layer at all. A `table_cell` question aimed at
those statement pages would cite evidence that extracts nothing.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from twfi.errors import ParsingError
from twfi.eval.sources import (
    DocumentCapability,
    StatementState,
    coverage,
    derive_capability,
)
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.baseline import parse_baseline
from twfi.parsing.figures import chart_candidates, detect_figures
from twfi.parsing.quality import DocumentQuality, assess_pages
from twfi.parsing.tables import extract_tables
from twfi.parsing.types import ParsedDocument
from twfi.paths import repo_paths
from twfi.protocol import DECLARED_DOCUMENTS, LOCKED_TYPE_COUNTS

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    limit: Annotated[
        int, typer.Option(help="Stop after this many documents (0 = all). For a quick check.")
    ] = 0,
) -> None:
    """Measure per-document question-type capability."""
    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)

    capabilities: dict[str, DocumentCapability] = {}
    documents = DECLARED_DOCUMENTS[:limit] if limit else DECLARED_DOCUMENTS

    for document in documents:
        record = lock.get(document.doc_id)
        if record is None or not record.local_path(paths.root).is_file():
            typer.echo(f"{document.doc_id:<18} not acquired -- skipped")
            continue
        pdf_path = record.local_path(paths.root)

        try:
            parsed = parse_baseline(pdf_path, document.doc_id)
            tables = extract_tables(pdf_path)
            figures = detect_figures(pdf_path)
        except ParsingError as exc:
            typer.echo(f"{document.doc_id:<18} unreadable: {exc}")
            continue

        page_texts = _page_texts(parsed)
        quality = assess_pages(document.doc_id, page_texts)
        table_regions = [(table.page, table.bbox) for table in tables]
        charts = chart_candidates(figures, table_regions)
        labelled = sum(1 for chart in charts if chart.numeric_labels >= 3)

        capability = derive_capability(
            doc_id=document.doc_id,
            verdict=quality.verdict,
            legible_pages=quality.readable_pages,
            tables=len(tables),
            tables_with_unit=sum(1 for table in tables if table.unit is not None),
            labelled_charts=labelled,
            statements=_statement_state(quality),
            image_only_runs=quality.image_only_runs,
        )
        capabilities[document.doc_id] = capability

        typer.echo(
            f"{document.doc_id:<18} {quality.pages:>4}p  "
            f"legible {quality.readable_pages:>4}  "
            f"tables {len(tables):>4} ({capability.tables_with_unit} w/unit)  "
            f"charts {labelled:>3}  "
            f"stmts {capability.statements:<18} "
            f"{len(capability.sources)} type(s)"
        )
        for note in capability.notes:
            typer.echo(f"      - {note}")

    typer.echo("")
    typer.echo("question type              documents that can source it")
    typer.echo("-" * 78)
    found = coverage(capabilities)
    for question_type in LOCKED_TYPE_COUNTS:
        if question_type == "unanswerable":
            typer.echo(f"{question_type:<26} (a property of the question, not of a document)")
            continue
        docs = found.get(question_type, [])
        mark = "  <<< NONE" if not docs else ""
        typer.echo(f"{question_type:<26} {len(docs)}: {', '.join(docs) or '-'}{mark}")

    target = paths.runs / "question_sources.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "documents": [c.to_json() for c in capabilities.values()],
                "coverage": found,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    typer.echo("")
    typer.echo(f"wrote: {target.relative_to(paths.root)}")


def _page_texts(parsed: ParsedDocument) -> list[str]:
    by_page: dict[int, list[str]] = {}
    for block in parsed.blocks:
        by_page.setdefault(block.page, []).append(block.text)
    if not by_page:
        return []
    return ["\n".join(by_page.get(page, ())) for page in range(1, max(by_page) + 1)]


def _statement_state(quality: DocumentQuality) -> StatementState:
    """How the statements relate to this filing.

    Three states, not two. "Absent" and "unreadable" were conflated at first, which
    labelled 2330-FY2024-AR -- a 股東會年報 that by design embeds no statements (D-012)
    -- as having an unreadable text layer. One is a property of Taiwanese filing
    practice; the other is a defect in this particular PDF.
    """
    if quality.verdict == "statements_not_machine_readable":
        return "image_only"
    if not quality.has_financial_statements:
        return "absent_by_design"
    return "readable"


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
