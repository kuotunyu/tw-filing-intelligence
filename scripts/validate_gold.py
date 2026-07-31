"""Check every gold file against the pre-registered protocol.

    uv run python scripts/validate_gold.py
    uv run python scripts/validate_gold.py --set locked --require-complete

Reports every violation at once rather than the first, because annotating 72 questions
and being told about one error per run would be a poor way to spend an afternoon.

`--require-complete` additionally demands the pre-registered composition, and is what
must pass before `scripts/freeze_protocol.py` may run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from twfi.eval.gold import GoldRecord, GoldSet, load_gold, set_problems
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


def _files() -> dict[GoldSet, Path]:
    paths = repo_paths()
    return {
        "dev": paths.dev_gold,
        "locked": paths.locked_gold,
        "probe": paths.locked_probes,
        "challenger": paths.chart_challenger,
    }


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="Validate one set only: dev, locked, probe, challenger.")
    ] = "all",
    require_complete: Annotated[
        bool,
        typer.Option(help="Fail if a set is absent or short of its pre-registered size."),
    ] = False,
) -> None:
    """Validate gold files and exit non-zero if any problem is found."""
    files = _files()
    if gold_set != "all":
        if gold_set not in files:
            typer.echo(f"unknown set {gold_set!r}; choose from {sorted(files)} or 'all'")
            raise typer.Exit(code=2)
        files = {name: path for name, path in files.items() if name == gold_set}

    total_problems = 0
    validated = 0
    for name, path in files.items():
        problems, count = _report(name, path, require_complete=require_complete)
        total_problems += problems
        validated += count

    typer.echo("")
    if total_problems:
        typer.echo(f"FAILED: {total_problems} problem(s)")
        raise typer.Exit(code=1)
    if validated == 0:
        # "Nothing to check" and "everything checks out" must not print the same line.
        typer.echo("no records to validate -- nothing has been annotated yet")
        return
    typer.echo(f"{validated} record(s) conform to the protocol")


def _report(name: GoldSet, path: Path, *, require_complete: bool) -> tuple[int, int]:
    """Validate one file. Returns ``(problems found, records validated)``."""
    typer.echo(f"=== {name} :: {path.name} ===")

    if not path.exists():
        if require_complete:
            typer.echo("  MISSING -- required before freeze")
            return 1, 0
        typer.echo("  not annotated yet")
        return 0, 0

    try:
        records = load_gold(path.read_text(encoding="utf-8").splitlines())
    except ValueError as exc:
        typer.echo(f"  UNPARSEABLE: {exc}")
        return 1, 0

    from twfi.protocol import LOCKED_TYPE_COUNTS

    counts = dict(LOCKED_TYPE_COUNTS) if (require_complete and name == "locked") else None
    problems = set_problems(records, gold_set=name, type_counts=counts)
    if require_complete:
        problems.extend(_completeness_problems(name, records))

    typer.echo(f"  records: {len(records)}")
    for question_type, count in sorted(_counts(records).items()):
        typer.echo(f"    {question_type:<26} {count}")

    for problem in problems:
        typer.echo(f"  - {problem}")
    if not problems:
        typer.echo("  ok")
    return len(problems), len(records)


def _counts(records: list[GoldRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.question_type] = counts.get(record.question_type, 0) + 1
    return counts


def _completeness_problems(name: GoldSet, records: list[GoldRecord]) -> list[str]:
    """Sizes that only matter once a set claims to be finished."""
    from twfi.protocol import CHALLENGER_ITEMS, DEV_TOTAL, LOCKED_TOTAL, PROBE_COUNT

    expected = {
        "dev": DEV_TOTAL,
        "locked": LOCKED_TOTAL,
        "probe": PROBE_COUNT,
        "challenger": CHALLENGER_ITEMS,
    }[name]
    if len(records) != expected:
        return [f"{name} set must hold {expected} items, has {len(records)}"]
    return []


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
