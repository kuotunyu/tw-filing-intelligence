"""Name downloaded filings from their own covers, not from the search field.

    uv run python scripts/identify_documents.py            # report only
    uv run python scripts/identify_documents.py --apply     # perform the renames

Drop the PDFs into data/raw/manual/ under whatever name the browser gave them, then
run this. It reads each cover, works out the company code and the fiscal year the
report states, and renames it to the name the manifest expects.

This exists because the MOPS index is keyed by the shareholders' meeting year, not
by the fiscal year: 資料年度 112 returns the 民國111年度年報. Naming files from the
query would mislabel every document by one year -- and a mislabelled locked-set
document silently changes what the study measured.
"""

from __future__ import annotations

from typing import Annotated

import typer

from twfi.io.identify import pdf_candidates, plan_renames
from twfi.io.manifest import load_document_manifest
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    apply: Annotated[bool, typer.Option(help="Actually rename the files.")] = False,
) -> None:
    """Identify every PDF in the manual drop folder and report its correct name."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    manifest = load_document_manifest(paths.documents_manifest)
    declared = {record.filename for record in manifest.documents}
    expected_by_name = {record.filename: record for record in manifest.documents}

    candidates = pdf_candidates(paths.manual_raw)
    if not candidates:
        typer.echo(f"no PDFs found in {paths.manual_raw.relative_to(paths.root)}")
        typer.echo("download the filings first: uv run python scripts/fetch_documents.py")
        raise typer.Exit(code=0)

    plans = plan_renames(candidates, declared)
    renamed = 0

    for plan in plans:
        typer.echo("")
        typer.echo(f"file        : {plan.path.name}")
        typer.echo(f"identity    : {plan.identity.describe()}")

        if plan.problem:
            typer.echo(f"PROBLEM     : {plan.problem}")
            continue
        if not plan.declared:
            typer.echo(f"UNDECLARED  : {plan.target_name} is not in documents.yaml")
            typer.echo("              the protocol declares exactly 7 documents; amend it first")
            continue

        record = expected_by_name[plan.target_name or ""]
        typer.echo(f"belongs to  : {record.doc_id} ({record.company.name}, {record.split} split)")

        if not plan.needs_rename:
            typer.echo("action      : already correctly named")
            continue

        target = plan.path.with_name(plan.target_name or "")
        if target.exists():
            typer.echo(f"CONFLICT    : {target.name} already exists; not overwriting")
            continue
        if apply:
            plan.path.rename(target)
            renamed += 1
            typer.echo(f"action      : renamed -> {target.name}")
        else:
            typer.echo(f"action      : would rename -> {target.name}  (re-run with --apply)")

    ready = [plan for plan in plans if plan.is_ready]
    typer.echo("")
    typer.echo(f"identified  : {len(ready)}/{len(plans)}")
    typer.echo(f"declared    : {len(declared)} documents in documents.yaml")
    if apply:
        typer.echo(f"renamed     : {renamed}")
        typer.echo("next        : uv run python scripts/fetch_documents.py")

    if any(plan.problem or not plan.declared for plan in plans):
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
