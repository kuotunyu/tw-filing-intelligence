"""Download the declared TWSE OpenAPI datasets and record their digests.

    uv run python scripts/fetch_twse_openapi.py
    uv run python scripts/fetch_twse_openapi.py --only twse-openapi-t187ap06_L_ci

Reads `data/manifests/structured.yaml` (declaration) and writes
`data/manifests/acquisition.lock.yaml` (record). Politeness, the host allowlist,
and the byte ceilings come from `twfi.io.http`.

Remember what these endpoints are: a single-period snapshot. They cannot supply
FY2023/FY2024 -- see docs/DATA_PROVENANCE.md 8.
"""

from __future__ import annotations

import json
from typing import Annotated

import typer

from twfi.io.acquire import fetch_structured_datasets
from twfi.io.http import PoliteClient
from twfi.io.manifest import (
    LOCK_HEADER,
    dump_yaml_model,
    load_acquisition_lock,
    load_structured_manifest,
)
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    only: Annotated[
        list[str] | None, typer.Option(help="Fetch only these dataset ids (repeatable).")
    ] = None,
) -> None:
    """Fetch every automated dataset declared in the structured manifest."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    structured = load_structured_manifest(paths.structured_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    with PoliteClient() as client:
        lock, messages = fetch_structured_datasets(
            structured, client, paths.root, lock, only=only or None
        )
        network = client.snapshot()

    dump_yaml_model(lock, paths.acquisition_lock, header=LOCK_HEADER)

    for message in messages:
        typer.echo(message)
    typer.echo("")
    typer.echo(f"lock   : {paths.acquisition_lock.relative_to(paths.root)}")
    typer.echo(f"records: {len(lock.records)}")
    typer.echo("network: " + json.dumps(network, ensure_ascii=False, sort_keys=True))

    if any(message.startswith("FAILED") for message in messages):
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
