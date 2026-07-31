"""Check whether each acquired filing can actually serve as evidence.

    uv run python scripts/check_document_quality.py

Run this before annotating anything. A filing with an unreadable text layer or with
its financial statements in a different file would otherwise look like a retrieval
failure or a numeric-route failure, and the study would draw the wrong conclusion
from it.

Writes results/runs/document_quality.json. CPU only, no network.
"""

from __future__ import annotations

import json

import pymupdf
import typer

from twfi.console import use_utf8_output
from twfi.io.manifest import load_acquisition_lock, load_document_manifest
from twfi.parsing.quality import assess_pages
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


def _page_texts(path: str) -> list[str]:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        return [str(document.load_page(index).get_text()) for index in range(document.page_count)]


@app.command()
def main() -> None:
    """Assess every acquired document and report which ones are usable."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    manifest = load_document_manifest(paths.documents_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    assessments = []
    for record in manifest.documents:
        target = record.local_path(paths.root)
        if lock.get(record.doc_id) is None or not target.is_file():
            typer.echo(f"skip {record.doc_id}: not acquired")
            continue
        assessments.append(assess_pages(record.doc_id, _page_texts(str(target))))

    if not assessments:
        typer.echo("nothing acquired yet")
        raise typer.Exit(code=1)

    header = f"{'doc_id':<18}{'pages':>6}{'chars/pg':>10}{'readable':>10}{'stmts':>7}  verdict"
    typer.echo("")
    typer.echo(header)
    typer.echo("-" * (len(header) + 22))
    for item in assessments:
        found = sum(1 for page in item.statement_pages.values() if page is not None)
        mark = "ok " if item.is_usable else "!! "
        typer.echo(
            f"{item.doc_id:<18}{item.pages:>6}{item.chars_per_page:>10.0f}"
            f"{item.readable_ratio:>9.0%}{found:>7}  {mark}{item.verdict}"
        )

    problems = [item for item in assessments if not item.is_usable]
    for item in problems:
        typer.echo("")
        typer.echo(f"{item.doc_id}: {item.verdict}")
        for reason in item.reasons:
            typer.echo(f"  - {reason}")

    target_path = paths.runs / "document_quality.json"
    target_path.write_text(
        json.dumps(
            {"documents": [item.to_json() for item in assessments]},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # The verdicts are facts about documents, not judgements about the study.
    # `missing_financial_statements` is expected for a FY2024 annual report -- those
    # are paired with a 財務報告書 on purpose (DECISIONS D-012). Only a broken text
    # layer makes a document unusable as evidence (D-013).
    unreadable = [
        item
        for item in assessments
        if item.verdict in {"unusable_text_layer", "partially_unusable_text_layer"}
    ]
    narrative_only = [
        item for item in assessments if item.verdict == "missing_financial_statements"
    ]

    typer.echo("")
    typer.echo(f"readable text layer : {len(assessments) - len(unreadable)}/{len(assessments)}")
    typer.echo(f"narrative only      : {len(narrative_only)} (expected; paired with a 財務報告書)")
    typer.echo(f"unusable as evidence: {len(unreadable)}")
    typer.echo(f"wrote               : {target_path.relative_to(paths.root)}")

    if unreadable:
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
