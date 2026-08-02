"""Freeze the protocol and the locked set: protocol §7, the step that permits touching LOCKED.

    uv run python scripts/freeze_protocol.py --dry-run     # show what would be frozen
    uv run python scripts/freeze_protocol.py --i-understand-this-is-irreversible

Writes `results/feasibility/protocol_lock.json`, holding a SHA-256 for the protocol document,
the locked gold set and probes, all three data manifests, and the model pins. After that,
`tests/test_protocol_lock.py` re-checks every digest on each `pytest` run, so an edit to any of
them fails the suite rather than passing quietly.

**This is a one-way door and the script is built to act like one.** Freezing is what makes the
study pre-registered: it is the difference between "the threshold was 5pp" and "the threshold was
5pp before we saw the numbers". So:

* it refuses to run when a lock already exists -- re-freezing is how a frozen protocol gets
  edited, and protocol §intro says a genuine change means a new `protocol_version` and a full
  re-run, not a second lock;
* it refuses on any leakage or gold-validation problem, because §5 and §6 come before §7 and a
  lock over a set that leaks freezes the leak;
* it refuses while `protocol_version` still ends in `-draft`, which is the version's own way of
  saying it is not ready;
* it refuses while the protocol document's own status is not `FINAL`, so the lock cannot make
  a contradictory `DRAFT` label permanent;
* it requires `--i-understand-this-is-irreversible`, spelled out rather than `--force`, because
  the flag is the last thing between an ordinary command and an unrepeatable one.

Nothing here reads a model, the network, or `.env`. `--dry-run` prints the digests it would
write and touches nothing, which is the safe way to see what a freeze would cover.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Annotated

import typer

from twfi.console import use_utf8_output
from twfi.errors import ProtocolLockError
from twfi.eval.gold import GoldRecord, GoldSet, load_gold, set_problems
from twfi.eval.leakage import leakage_problems
from twfi.eval.protocol_lock import LOCKED_ARTIFACTS, build_lock
from twfi.paths import RepoPaths, repo_paths
from twfi.protocol import LOCKED_TYPE_COUNTS, PROTOCOL_VERSION

app = typer.Typer(add_completion=False, help=__doc__)

#: The sets that must be clean before a freeze. `challenger` is absent because D-021 cancelled it.
_GOLD_SETS: tuple[GoldSet, ...] = ("dev", "locked", "probe")


def _protocol_status_problem(source: str) -> str | None:
    """Explain why the protocol document is not explicitly final, or return ``None``."""
    status_line = next(
        (line.strip() for line in source.splitlines() if line.strip().startswith("`status:")),
        None,
    )
    if status_line is None:
        return "protocol document has no explicit `status:` line; only FINAL may be frozen"
    status = status_line.strip("`").partition(":")[2].strip()
    if not status.upper().startswith("FINAL"):
        return (
            f"protocol document status is {status!r}; set it to FINAL before freeze so the "
            "locked document does not describe itself as a draft"
        )
    return None


@app.command()
def main(
    confirmed: Annotated[
        bool,
        typer.Option(
            "--i-understand-this-is-irreversible",
            help="Required to write the lock. Spelled out on purpose.",
        ),
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print what would be frozen and write nothing.")
    ] = False,
    version: Annotated[
        str, typer.Option("--version", help="Overrides PROTOCOL_VERSION. For a 2.x re-freeze.")
    ] = "",
) -> None:
    """Check every precondition, then write the lock."""
    paths = repo_paths()
    protocol_version = version or PROTOCOL_VERSION

    if paths.protocol_lock_json.is_file():
        typer.echo(
            f"{paths.protocol_lock_json.relative_to(paths.root)} already exists: this protocol "
            "is frozen.\nA change to a frozen protocol means a new protocol_version and a full "
            "re-run of every locked configuration (protocol §intro), not a second lock. Delete "
            "nothing; open 2.x."
        )
        raise typer.Exit(code=2)

    problems: list[str] = []

    if protocol_version.endswith("-draft"):
        problems.append(
            f"protocol_version is {protocol_version!r}: the version says the protocol is a draft. "
            "Settle it in docs/FEASIBILITY_PROTOCOL.md and src/twfi/protocol.py, or pass "
            "--version explicitly if the draft suffix is what is being frozen."
        )

    if paths.protocol_doc.is_file():
        try:
            status_problem = _protocol_status_problem(
                paths.protocol_doc.read_text(encoding="utf-8")
            )
        except OSError as exc:
            problems.append(f"protocol document status cannot be read: {exc}")
        else:
            if status_problem is not None:
                problems.append(status_problem)

    missing = [
        relative
        for relative, _mode, optional in LOCKED_ARTIFACTS
        if not optional and not (paths.root / relative).is_file()
    ]
    problems.extend(f"required artifact is missing: {relative}" for relative in missing)

    loaded: dict[GoldSet, list[GoldRecord]] = {}
    for name in _GOLD_SETS:
        source = _gold_path(paths, name)
        if source is None or not source.is_file():
            problems.append(f"gold set {name!r} does not exist at its expected path")
            continue
        try:
            records = list(load_gold(source.read_text(encoding="utf-8").splitlines()))
        except (ValueError, OSError) as exc:
            problems.append(f"gold set {name!r} does not load: {exc}")
            continue
        loaded[name] = records
        # Freezing is the claim that these sets are finished, so they are validated as finished:
        # the locked composition must match LOCKED_TYPE_COUNTS, and every set must satisfy the
        # audit rule. `validate_gold.py` relaxes both while a set is still being annotated --
        # here a partial set is not progress, it is a set about to become unchangeable.
        problems.extend(
            f"{name}: {problem}"
            for problem in set_problems(
                records,
                gold_set=name,
                type_counts=dict(LOCKED_TYPE_COUNTS) if name == "locked" else None,
                require_audit=True,
            )
        )

    if loaded:
        # §6 before §7: a lock over a set that leaks locked material into tuning freezes the
        # leak, and the whole point of the locked set is that nothing was tuned on it.
        problems.extend(f"leakage: {problem}" for problem in leakage_problems(loaded))

    if problems:
        typer.echo(f"{len(problems)} problem(s); nothing frozen:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)

    frozen_at = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    try:
        lock = build_lock(paths.root, protocol_version, frozen_at)
    except ProtocolLockError as exc:
        typer.echo(str(exc))
        raise typer.Exit(code=1) from exc

    typer.echo(f"protocol_version {lock.protocol_version}, {len(lock.entries)} artifact(s):")
    for entry in lock.entries:
        typer.echo(f"  {entry.sha256}  {entry.mode:<5} {entry.path}")
    for name, records in sorted(loaded.items()):
        typer.echo(f"  {name}: {len(records)} record(s), no validation or leakage problem")

    if dry_run:
        typer.echo("")
        typer.echo("--dry-run: nothing written. The protocol is not frozen.")
        return
    if not confirmed:
        typer.echo("")
        typer.echo(
            "every precondition passes, and nothing was written. To freeze, pass\n"
            "  --i-understand-this-is-irreversible\n"
            "After that the locked questions, answers, tolerances, thresholds and model pins "
            "cannot change, whatever the results turn out to be."
        )
        raise typer.Exit(code=2)

    lock.write(paths.protocol_lock_json)
    typer.echo("")
    typer.echo(f"froze {len(lock.entries)} artifact(s) at {frozen_at}")
    typer.echo(f"wrote {paths.protocol_lock_json.relative_to(paths.root)}")
    typer.echo("pytest now re-checks these digests on every run. LOCKED may be touched.")


def _gold_path(paths: RepoPaths, name: GoldSet) -> Path | None:
    """The file a gold set lives in, or ``None`` if this script does not know."""
    return {
        "dev": paths.dev_gold,
        "locked": paths.locked_gold,
        "probe": paths.locked_probes,
    }.get(name)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
