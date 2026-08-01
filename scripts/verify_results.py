"""Recompute every number in summary.json from the raw run artifacts -- gate G9.

    uv run python scripts/verify_results.py
    uv run python scripts/verify_results.py --raw path/to/runs --summary path/to/summary.json
    uv run python scripts/verify_results.py --dry-run

Reads `results/feasibility/summary.json`, the graded records under `results/runs/<run>/`, and
`results/feasibility/protocol_lock.json`; prints every figure that does not reproduce and
exits 1 if there are any.

Exit codes are the interface, so they are worth stating: **0** nothing to report, **1** at
least one figure does not reproduce (or an artifact is unreadable), **2** the inputs do not
exist yet. 2 is not 0 on purpose -- "the locked run has not happened" must never be reported
in the same breath as "the results reproduce".

The judgement is in `twfi.eval.results`, which is pure and tested; this file loads, prints and
picks an exit code. The split matters more here than elsewhere, because this script is what
earns `summary["checks"]["results_reproducible"]`, the boolean gate G9 reads. It deliberately
does **not** write that boolean into summary.json: a script that both decided reproducibility
and edited the file it judges would be able to make itself true. It writes its own findings
beside the summary and leaves the flag to whoever assembles the summary -- who then has to
paste a `false` next to a list of reasons.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.errors import ResultIntegrityError
from twfi.eval.results import load_artifacts, verify
from twfi.io.hashing import sha256_text_file
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


def _load_summary(path: Path) -> dict[str, Any]:
    """Read summary.json, or exit 2. A summary that cannot be parsed has not been produced."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"{path} is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    if not isinstance(payload, dict):
        typer.echo(f"{path} must hold a JSON object")
        raise typer.Exit(code=2)
    return payload


@app.command()
def main(
    summary_path: Annotated[
        Path | None, typer.Option("--summary", help="Defaults to results/feasibility/summary.json.")
    ] = None,
    raw_dir: Annotated[
        Path | None,
        typer.Option("--raw", help="Directory of run artifacts; defaults to results/runs."),
    ] = None,
    lock_path: Annotated[
        Path | None,
        typer.Option("--lock", help="Defaults to results/feasibility/protocol_lock.json."),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run", help="Print the findings without writing them beside summary.json."
        ),
    ] = False,
) -> None:
    """Verify that summary.json is a report of the run rather than a claim about it."""
    paths = repo_paths()
    summary_file = summary_path or paths.summary_json
    runs_dir = raw_dir or paths.runs
    lock_file = lock_path or paths.protocol_lock_json

    absent = [
        f"{label}: {target}"
        for label, target, exists in (
            ("summary", summary_file, summary_file.is_file()),
            ("raw artifacts", runs_dir, runs_dir.is_dir()),
            ("protocol lock", lock_file, lock_file.is_file()),
        )
        if not exists
    ]
    if absent:
        typer.echo("cannot verify results; the locked run has not happened yet:")
        for line in absent:
            typer.echo(f"  missing {line}")
        typer.echo("Protocol 5: freeze, then run F0..F7 on the locked set, then verify.")
        raise typer.Exit(code=2)

    summary = _load_summary(summary_file)
    try:
        artifacts = load_artifacts(runs_dir)
    except ResultIntegrityError as exc:
        # Not exit 2: the artifact exists and is broken, which is a G9 failure rather than
        # work that has not happened yet.
        typer.echo(f"unreadable run artifact: {exc}")
        raise typer.Exit(code=1) from exc

    for run, records in sorted(artifacts.runs.items()):
        typer.echo(f"  read {len(records):>3} graded record(s) from {run}/")
    if not artifacts.runs:
        typer.echo(f"  read no graded records at all under {runs_dir}")

    problems = verify(
        summary,
        artifacts.runs,
        expected_lock_sha256=sha256_text_file(lock_file),
        resources=artifacts.resources,
    )

    typer.echo("")
    for problem in problems:
        typer.echo(f"  [{problem.kind}] {problem}")

    payload = {
        "summary": str(summary_file),
        "raw": str(runs_dir),
        "records_per_run": {run: len(records) for run, records in sorted(artifacts.runs.items())},
        "reproducible": not problems,
        "problems": [problem.to_json() for problem in problems],
    }
    if dry_run:
        typer.echo("--dry-run: results_verification.json not written")
    else:
        destination = summary_file.parent / "results_verification.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        typer.echo(f"wrote: {destination}")

    typer.echo("")
    if problems:
        typer.echo(f"{len(problems)} figure(s) in {summary_file.name} do not reproduce.")
        typer.echo("Set checks.results_reproducible to false: G9 fails, and the report keeps")
        typer.echo("the negative result. Fixing the numbers means re-running, not editing them.")
        raise typer.Exit(code=1)
    typer.echo(f"every figure in {summary_file.name} recomputes from the raw artifacts, and the")
    typer.echo("protocol lock hash matches. checks.results_reproducible may be set true.")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
