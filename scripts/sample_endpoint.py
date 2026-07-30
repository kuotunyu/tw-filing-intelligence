"""Inspect one TWSE OpenAPI endpoint: coverage, periods, and our companies' rows.

The question this answers is a feasibility question, not a curiosity: the
structured numeric route can only serve cross-period questions if the endpoint
actually carries more than one period. Guessing that from the schema is not
enough -- ``年度``/``季別`` fields exist even on snapshot endpoints.

    uv run python scripts/sample_endpoint.py /opendata/t187ap06_L_ci
    uv run python scripts/sample_endpoint.py /opendata/t187ap07_L_fh --company 2882

Findings from this script belong in ``docs/DATA_PROVENANCE.md``.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Annotated, Any

import typer

from twfi.io.http import PoliteClient
from twfi.paths import repo_paths
from twfi.protocol import COMPANIES

BASE = "https://openapi.twse.com.tw/v1"

app = typer.Typer(add_completion=False, help=__doc__)


def _period_key(row: dict[str, Any]) -> str:
    year = str(row.get("年度", "?"))
    quarter = str(row.get("季別", "?"))
    return f"{year}Q{quarter}"


@app.command()
def main(
    path: Annotated[str, typer.Argument(help="Endpoint path, e.g. /opendata/t187ap06_L_ci")],
    company: Annotated[
        str | None,
        typer.Option(help="Company code to show in full; defaults to all study companies"),
    ] = None,
    save: Annotated[
        bool, typer.Option(help="Save the raw response under data/raw/structured/.")
    ] = False,
) -> None:
    """Fetch one endpoint and report what it actually contains."""
    paths = repo_paths()
    url = f"{BASE}{path if path.startswith('/') else '/' + path}"

    with PoliteClient() as client:
        payload, result = client.get_bytes(url)
        network = client.snapshot()

    rows = json.loads(payload)
    if not isinstance(rows, list):
        typer.echo(f"unexpected payload type: {type(rows).__name__}")
        raise typer.Exit(code=1)

    typer.echo(f"url         : {url}")
    typer.echo(f"sha256      : {result.sha256}")
    typer.echo(f"bytes       : {result.num_bytes}")
    typer.echo(f"retrieved_at: {result.retrieved_at}")
    typer.echo(f"rows        : {len(rows)}")

    if not rows:
        typer.echo("empty response")
        raise typer.Exit(code=0)

    first = rows[0]
    if isinstance(first, dict):
        typer.echo(f"columns     : {len(first)}")
        periods = Counter(_period_key(row) for row in rows if isinstance(row, dict))
        typer.echo(f"periods     : {dict(sorted(periods.items()))}")
        typer.echo(
            f"period count: {len(periods)}  <-- 1 means this endpoint is a single-period snapshot"
        )

        wanted = {company} if company else {c.code for c in COMPANIES}
        for row in rows:
            if isinstance(row, dict) and str(row.get("公司代號")) in wanted:
                typer.echo("")
                typer.echo(f"--- {row.get('公司代號')} {row.get('公司名稱')} ---")
                typer.echo(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True))

    if save:
        raw_dir = paths.raw / "structured"
        raw_dir.mkdir(parents=True, exist_ok=True)
        target = raw_dir / (path.strip("/").replace("/", "_") + ".json")
        target.write_bytes(payload)
        typer.echo(f"saved       : {target}")

    typer.echo("network     : " + json.dumps(network, ensure_ascii=False, sort_keys=True))


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
