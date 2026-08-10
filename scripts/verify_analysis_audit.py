"""Verify the committed post-hoc analysis audit without models, APIs, raw PDFs, or writes.

uv run python scripts/verify_analysis_audit.py
"""

from __future__ import annotations

import typer

from twfi.console import use_utf8_output
from twfi.eval.analysis_audit import verify_committed_analysis_audit
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main() -> None:
    """Exit zero only when the audit is an exact deterministic recomputation."""
    problems = verify_committed_analysis_audit(repo_paths().root)
    if problems:
        typer.echo("analysis audit verification failed:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)
    typer.echo("verified: locked predictions -> runtime regrade -> protocol-literal audit")
    typer.echo("verified: frozen artifacts and the official NO_GO result remain unchanged")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
