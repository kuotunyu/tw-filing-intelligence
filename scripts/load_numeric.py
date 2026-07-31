"""Load the acquired TWSE OpenAPI datasets into the numeric store.

    uv run python scripts/load_numeric.py

Reads the JSON already fetched into data/raw/structured/ and writes
data/duckdb/numeric.duckdb. CPU only, no network -- the fetch already happened and
its digests are in the acquisition lock.

Load order matters. The per-industry statement endpoints (``_ci`` / ``_fh``) run
first because they are the authoritative source of a company's industry schema; the
cross-industry aggregates run second and inherit it, so 2882 is not relabelled as a
general-industry issuer by an endpoint that simply covers everyone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import typer

from twfi.errors import NumericRouteError
from twfi.io.manifest import load_acquisition_lock, load_structured_manifest
from twfi.numeric.loaders import OPENAPI_DATASETS, load_openapi_rows
from twfi.numeric.store import IndustrySchema, NumericStore
from twfi.paths import repo_paths
from twfi.protocol import COMPANIES

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    rebuild: bool = typer.Option(True, help="Delete and rebuild the database."),
) -> None:
    """Build data/duckdb/numeric.duckdb from the acquired OpenAPI datasets."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    structured = load_structured_manifest(paths.structured_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)
    study_codes = {company.code for company in COMPANIES}

    target = paths.duckdb / "numeric.duckdb"
    if rebuild:
        target.unlink(missing_ok=True)

    declared = {dataset.dataset_id: dataset for dataset in structured.datasets}
    ordered = sorted(
        (name for name in OPENAPI_DATASETS if name in declared),
        key=lambda name: not OPENAPI_DATASETS[name].declares_industry,
    )

    stamp = datetime.now(UTC).isoformat()
    schemas: dict[str, IndustrySchema] = {}
    total = 0

    with NumericStore(target) as store:
        for dataset_id in ordered:
            record = lock.get(dataset_id)
            if record is None:
                typer.echo(f"skip {dataset_id}: not acquired")
                continue
            path = record.local_path(paths.root)
            if not path.is_file():
                typer.echo(f"skip {dataset_id}: {record.relative_path} is missing")
                continue

            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                typer.echo(f"skip {dataset_id}: payload is not a row array")
                continue

            companies, items = load_openapi_rows(
                dataset_id,
                rows,
                source_url=declared[dataset_id].endpoint,
                company_codes=study_codes,
                industry_schema_by_company=schemas,
            )
            store.add_companies(companies)
            store.add_line_items(items)
            store.record_source(
                "openapi_current",
                dataset_id,
                loaded_at=stamp,
                rows_loaded=len(items),
                source_url=declared[dataset_id].endpoint,
                sha256=record.sha256,
            )
            for company in companies:
                schemas[company.code] = company.industry_schema
            total += len(items)
            typer.echo(
                f"{dataset_id:<32} {len(items):>5} figures "
                f"for {len({item.company_code for item in items})} companies"
            )

        typer.echo("")
        for company in COMPANIES:
            try:
                schema = store.industry_schema_of(company.code)
            except NumericRouteError:
                typer.echo(f"  {company.code} {company.name}: no figures loaded")
                continue
            accounts = store.accounts_for(company.code)
            typer.echo(
                f"  {company.code} {company.name:<8} {schema:<18} {len(accounts):>3} accounts"
            )

        typer.echo("")
        typer.echo(f"figures : {total}")
        typer.echo(f"database: {target.relative_to(paths.root)}")


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
