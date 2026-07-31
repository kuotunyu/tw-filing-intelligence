"""Check that nothing from the locked side can have influenced a tuning decision.

    uv run python scripts/check_leakage.py

This is step 6 of the protocol's execution order and must pass before
`scripts/freeze_protocol.py` runs. It checks company and document disjointness, that no
question appears on both sides in reworded form, that the chart challenger draws only on
dev, and that every gold answer is attributed to a human.

Absent files are reported, not treated as passing: "no leakage" and "nothing annotated"
are different states and must not print the same result.
"""

from __future__ import annotations

from pathlib import Path

import typer

from twfi.console import use_utf8_output
from twfi.eval.gold import GoldRecord, GoldSet, load_gold
from twfi.eval.leakage import NEAR_DUPLICATE_THRESHOLD, leakage_problems
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main() -> None:
    """Report every leakage vector, and exit non-zero if any is present."""
    paths = repo_paths()
    files: dict[GoldSet, Path] = {
        "dev": paths.dev_gold,
        "locked": paths.locked_gold,
        "probe": paths.locked_probes,
        "challenger": paths.chart_challenger,
    }

    sets: dict[GoldSet, list[GoldRecord]] = {}
    unreadable = 0
    for name, path in files.items():
        if not path.exists():
            typer.echo(f"{name:<11} not annotated yet ({path.name})")
            sets[name] = []
            continue
        try:
            sets[name] = load_gold(path.read_text(encoding="utf-8").splitlines())
        except ValueError as exc:
            typer.echo(f"{name:<11} UNPARSEABLE: {exc}")
            unreadable += 1
            sets[name] = []
            continue
        typer.echo(f"{name:<11} {len(sets[name])} record(s)")

    typer.echo("")
    typer.echo(f"near-duplicate threshold: bigram overlap >= {NEAR_DUPLICATE_THRESHOLD}")
    problems = leakage_problems(sets)

    if problems:
        typer.echo("")
        typer.echo("LEAKAGE PROBLEMS:")
        for problem in problems:
            typer.echo(f"  - {problem}")

    annotated = sum(len(records) for records in sets.values())
    typer.echo("")
    if unreadable or problems:
        typer.echo(f"FAILED: {len(problems) + unreadable} problem(s)")
        raise typer.Exit(code=1)
    if annotated == 0:
        typer.echo("nothing annotated yet -- this is not a passing leakage check")
        raise typer.Exit(code=1)
    typer.echo(f"no leakage detected across {annotated} annotated record(s)")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
