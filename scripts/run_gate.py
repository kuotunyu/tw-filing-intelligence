"""Apply the frozen GO / NO-GO gates to summary.json and write the verdict.

    uv run python scripts/run_gate.py
    uv run python scripts/run_gate.py --summary path/to/summary.json --dry-run

Reads `results/feasibility/summary.json`, writes `results/feasibility/GO_NO_GO.json`, and
exits non-zero when the verdict is not GO -- so a pipeline cannot proceed past a NO_GO by
ignoring output.

The judgement itself is in `twfi.eval.gates`, which is pure and tested. This file only
loads, prints and writes. That split is the point: protocol 4 says nobody may overwrite the
verdict by hand, and a verdict computed inside a script nothing imports would be much easier
to quietly adjust.

A missing metric fails its gate. That is deliberate and worth stating where it is visible:
if absent evidence passed, deleting a number would be the cheapest route to GO.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from twfi.console import use_utf8_output
from twfi.eval.gates import decide, evaluate
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    summary_path: Annotated[
        Path | None, typer.Option("--summary", help="Defaults to results/feasibility/summary.json.")
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the verdict without writing GO_NO_GO.json.")
    ] = False,
) -> None:
    """Evaluate every gate and record the decision."""
    paths = repo_paths()
    source = summary_path or (paths.feasibility / "summary.json")
    if not source.is_file():
        typer.echo(f"{source} does not exist yet; run the locked evaluation first")
        raise typer.Exit(code=2)

    try:
        summary = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"{source} is not valid JSON: {exc}")
        raise typer.Exit(code=2) from exc
    if not isinstance(summary, dict):
        typer.echo(f"{source} must hold a JSON object")
        raise typer.Exit(code=2)

    outcomes = evaluate(summary)
    verdict = decide(outcomes)

    for outcome in outcomes:
        mark = "PASS" if outcome.passed else "FAIL"
        kind = "" if outcome.kind == "hard" else " (soft)"
        typer.echo(f"  {mark}  {outcome.gate}  {outcome.name}{kind}")
        typer.echo(f"        {outcome.detail}")
        for line in outcome.observed:
            typer.echo(f"        - {line}")

    typer.echo("")
    typer.echo(f"VERDICT: {verdict}")
    if verdict == "CONDITIONAL_GO":
        typer.echo("Every hard gate passed and G10 did not: this is a resource result, not a")
        typer.echo("capability result, and the report must say which limit was exceeded.")
    elif verdict == "NO_GO":
        typer.echo("A hard gate failed. Protocol 4: do not change questions, answers,")
        typer.echo("tolerances or thresholds to reach GO, and do not remove the negative")
        typer.echo("result. State the smallest next research question instead.")

    payload = {
        "verdict": verdict,
        "protocol_lock_sha256": summary.get("protocol_lock_sha256"),
        "gates": [outcome.to_json() for outcome in outcomes],
    }
    if dry_run:
        typer.echo("")
        typer.echo("--dry-run: GO_NO_GO.json not written")
    else:
        destination = paths.feasibility / "GO_NO_GO.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        typer.echo("")
        typer.echo(f"wrote: {destination.relative_to(paths.root)}")

    if verdict != "GO":
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
