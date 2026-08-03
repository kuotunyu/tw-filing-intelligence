"""Verify the committed evidence chain without models, APIs, PDFs, or GPU work.

    uv run python scripts/verify_evidence.py

The command checks the frozen protocol, recomputes summary metrics from committed graded
records, compares both committed JSON artifacts with fresh deterministic payloads, and
requires the registered NO_GO verdict. It never writes a file.
"""

from __future__ import annotations

import typer

from twfi.console import use_utf8_output
from twfi.eval.evidence import verify_committed_evidence
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main() -> None:
    """Exit zero only when every committed evidence link is equivalent."""
    problems = verify_committed_evidence(repo_paths().root)
    if problems:
        typer.echo("committed evidence verification failed:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)
    typer.echo("verified: frozen protocol -> raw runs -> summary -> G1-G10 -> NO_GO")
    typer.echo("verified: results_verification paths are portable and repository-relative")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
