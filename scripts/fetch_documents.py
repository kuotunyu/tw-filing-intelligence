"""Record hand-placed filings, and print exact instructions for what is missing.

    uv run python scripts/fetch_documents.py

There is no automated download here, and that is a decision rather than a gap:
the new MOPS is a single-page app whose data API is unpublished, and the
server-rendered alternative is a POST form with unpublished `step` semantics. For
seven documents, neither is worth doing. Manual placement with a recorded
source page and a pinned SHA-256 is fully reproducible and satisfies gate G1.
See docs/DATA_PROVENANCE.md 8 and DECISIONS D-010.
"""

from __future__ import annotations

import typer

from twfi.io.acquire import expected_artifacts, register_manual_artifacts
from twfi.io.manifest import (
    LOCK_HEADER,
    dump_yaml_model,
    load_acquisition_lock,
    load_document_manifest,
    load_structured_manifest,
)
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main() -> None:
    """Hash every placed artifact; list the ones still needed."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    documents = load_document_manifest(paths.documents_manifest)
    structured = load_structured_manifest(paths.structured_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    artifacts = expected_artifacts(documents, structured)
    lock, messages, missing = register_manual_artifacts(artifacts, paths.root, lock)
    dump_yaml_model(lock, paths.acquisition_lock, header=LOCK_HEADER)

    for message in messages:
        typer.echo(message)

    required_missing = [artifact for artifact in missing if artifact.required]
    optional_missing = [artifact for artifact in missing if not artifact.required]

    if missing:
        typer.echo("")
        typer.echo("Still needed -- download from the source page and save to the path shown:")
        for artifact in required_missing:
            typer.echo("")
            typer.echo(f"  [required] {artifact.id}")
            typer.echo(f"    source : {artifact.source_page}")
            typer.echo(f"    save to: {artifact.relative_path.as_posix()}")
            typer.echo(f"    how    : {artifact.hint}")
        for artifact in optional_missing:
            typer.echo("")
            typer.echo(f"  [optional] {artifact.id}")
            typer.echo(f"    source : {artifact.source_page}")
            typer.echo(f"    save to: {artifact.relative_path.as_posix()}")

    typer.echo("")
    typer.echo(f"lock              : {paths.acquisition_lock.relative_to(paths.root)}")
    typer.echo(f"required placed   : {len(artifacts) - len(missing)}/{len(artifacts)}")
    typer.echo(f"required missing  : {len(required_missing)}")
    typer.echo(f"optional missing  : {len(optional_missing)}")

    if required_missing:
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
