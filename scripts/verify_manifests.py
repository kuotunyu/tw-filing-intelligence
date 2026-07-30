"""Verify that every acquired artifact still matches the lock, and report coverage.

    uv run python scripts/verify_manifests.py
    uv run python scripts/verify_manifests.py --require-all   # gate G1 evidence

This is the check behind gate G1 ("data acquisition is reproducible"): re-hash
everything, compare to the lock, and say plainly what has not been acquired yet.
Also regenerates `docs/reference/provenance_table.md`.
"""

from __future__ import annotations

from typing import Annotated

import typer

from twfi.io.acquire import expected_artifacts, provenance_table
from twfi.io.manifest import (
    load_acquisition_lock,
    load_document_manifest,
    load_structured_manifest,
    verify_acquisition,
)
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    require_all: Annotated[
        bool, typer.Option(help="Fail unless every required artifact is acquired.")
    ] = False,
    write_table: Annotated[
        bool, typer.Option(help="Regenerate docs/reference/provenance_table.md.")
    ] = True,
) -> None:
    """Re-hash acquired artifacts and report what is missing."""
    paths = repo_paths()

    documents = load_document_manifest(paths.documents_manifest)
    structured = load_structured_manifest(paths.structured_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    required_ids = {
        artifact.id for artifact in expected_artifacts(documents, structured) if artifact.required
    }
    required_ids |= {dataset.dataset_id for dataset in structured.automated()}

    integrity = verify_acquisition(lock, paths.root)
    coverage = verify_acquisition(lock, paths.root, expected_ids=required_ids)
    not_acquired = [problem for problem in coverage if problem not in integrity]

    typer.echo(f"declared documents : {len(documents.documents)}")
    typer.echo(f"declared datasets  : {len(structured.datasets)}")
    typer.echo(f"acquired artifacts : {len(lock.records)}")
    typer.echo("")

    if integrity:
        typer.echo("INTEGRITY PROBLEMS (an acquired artifact no longer matches its digest):")
        for problem in integrity:
            typer.echo(f"  - {problem}")
    else:
        typer.echo("integrity: every acquired artifact matches its recorded SHA-256")

    if not_acquired:
        typer.echo("")
        typer.echo("NOT ACQUIRED YET:")
        for problem in not_acquired:
            typer.echo(f"  - {problem}")

    if write_table:
        target = paths.docs / "reference" / "provenance_table.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(provenance_table(documents, structured, lock), encoding="utf-8")
        typer.echo("")
        typer.echo(f"wrote: {target.relative_to(paths.root)}")

    if integrity or (require_all and not_acquired):
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
