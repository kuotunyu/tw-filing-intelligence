"""Pick a reproducible sample of model-drafted gold for a person to check.

    uv run python scripts/audit_gold.py --set locked            # show the sample
    uv run python scripts/audit_gold.py --set locked --render   # and render its pages
    uv run python scripts/audit_gold.py --set locked --accept LOCK-0021 LOCK-0024
    uv run python scripts/audit_gold.py --set locked --reject LOCK-0022

Gold may be drafted by a model reading rendered page images (D-019). The risk that
introduces is not bad transcription -- a machine is better at digits than a person, as
PROBE-0004 showed -- but bad *question selection*: a drafter that also chooses the
questions can drift toward what the pipeline handles well, and nothing in the record
would reveal it.

The audit sample is the defence, so it must not be choosable by the drafter. The sample
is drawn with a fixed seed taken from the protocol's decoding seed, so the same set and
the same size always yield the same records, and anyone can re-derive which ones should
have been checked. Presenting a hand-picked "sample" would therefore be visible.

`--accept` marks records audited. `--reject` clears the flag and is meant to be followed
by redrafting the whole question type, not just the one record: a drafting habit that
produced one bad question probably produced others.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Annotated

import typer

from twfi.eval.gold import GoldRecord, GoldSet, composition, load_gold
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

#: The protocol's decoding seed (2.5), reused so the sample is fixed by the protocol
#: rather than by whoever runs this.
AUDIT_SEED = 20260731

#: Protocol 1.5 as amended by D-019. Eight of the locked set is roughly a quarter, which
#: is enough that a systematic drafting bias would show up in more than one record.
DEFAULT_SAMPLE = 8


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
    gold_set: Annotated[str, typer.Option("--set", help="Which set to audit.")] = "locked",
    size: Annotated[int, typer.Option(help="Sample size.")] = DEFAULT_SAMPLE,
    render: Annotated[
        bool, typer.Option("--render", help="Also render the sampled records' pages.")
    ] = False,
    accept: Annotated[
        list[str] | None, typer.Option(help="Mark these question ids audited.")
    ] = None,
    reject: Annotated[
        list[str] | None, typer.Option(help="Clear the audited flag on these ids.")
    ] = None,
) -> None:
    """Show, render, or record the outcome of a gold audit."""
    paths = repo_paths()
    files = _files()
    if gold_set not in files:
        typer.echo(f"unknown set {gold_set!r}; choose from {sorted(files)}")
        raise typer.Exit(code=2)
    path = files[gold_set]
    if not path.is_file():
        typer.echo(f"{path.relative_to(paths.root)} does not exist yet")
        raise typer.Exit(code=2)

    if accept or reject:
        changed = _record_outcome(path, accept or [], reject or [])
        typer.echo(f"updated {changed} record(s) in {path.relative_to(paths.root)}")
        _report_composition(path)
        return

    records = load_gold(path.read_text(encoding="utf-8").splitlines())
    sample = audit_sample(records, size=size)
    if not sample:
        typer.echo("nothing to audit: every record in this set was written by a person")
        _report_composition(path)
        return

    typer.echo(f"audit sample for {gold_set} (seed {AUDIT_SEED}, size {size}):")
    typer.echo("")
    for record in sample:
        mark = "[checked]" if record.audited else "[ TODO  ]"
        pages = ", ".join(f"p{page}" for page in record.page_numbers)
        typer.echo(f"  {mark} {record.question_id}  {record.question_type}")
        typer.echo(f"            {record.source_document[0]} {pages}")
        typer.echo(f"            Q: {record.question}")
        typer.echo(f"            A: {record.answer}")
        if record.derived_from:
            typer.echo(f"            from: {list(record.derived_from)}")
        typer.echo("")

    if render:
        _render(sample)

    typer.echo("For each: open the page, check the answer is right and the question is one")
    typer.echo("a reader would actually ask. Then:")
    typer.echo(f"  uv run python scripts/audit_gold.py --set {gold_set} --accept <ids...>")
    _report_composition(path)


def audit_sample(records: list[GoldRecord], *, size: int) -> list[GoldRecord]:
    """A reproducible sample of the model-drafted records, in id order.

    Seeded from the protocol so the drafter cannot choose which records get checked. Sorts
    by id first so the input order -- which the drafter controls -- cannot change the draw.
    """
    drafted = sorted(
        (record for record in records if record.annotator != "human"),
        key=lambda record: record.question_id,
    )
    if len(drafted) <= size:
        return drafted
    # Reproducibility is the requirement here, not unpredictability. A cryptographic
    # source would make the sample unauditable: nobody could re-derive which records
    # should have been checked, which is the whole point of seeding it.
    chosen = random.Random(AUDIT_SEED).sample(range(len(drafted)), size)  # noqa: S311
    return [drafted[index] for index in sorted(chosen)]


def _record_outcome(path: Path, accept: list[str], reject: list[str]) -> int:
    accepted, rejected = set(accept), set(reject)
    overlap = accepted & rejected
    if overlap:
        typer.echo(f"cannot both accept and reject {sorted(overlap)}")
        raise typer.Exit(code=2)

    out: list[str] = []
    changed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            out.append(line)
            continue
        payload = json.loads(stripped)
        qid = str(payload.get("question_id"))
        if qid in accepted and not payload.get("audited"):
            payload["audited"] = True
            changed += 1
        elif qid in rejected and payload.get("audited"):
            payload["audited"] = False
            changed += 1
        out.append(json.dumps(payload, ensure_ascii=False))
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def _render(sample: list[GoldRecord]) -> None:
    """Render each sampled record's pages, named by question id."""
    from twfi.errors import ParsingError
    from twfi.io.manifest import load_acquisition_lock

    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    target = paths.runs / "pages"
    target.mkdir(parents=True, exist_ok=True)

    import pymupdf

    for record in sample:
        doc_id = record.source_document[0]
        acquired = lock.get(doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            typer.echo(f"  {record.question_id}: {doc_id} not acquired")
            continue
        try:
            with pymupdf.open(acquired.local_path(paths.root)) as document:  # type: ignore[no-untyped-call]
                for page in record.page_numbers:
                    if not 1 <= page <= document.page_count:
                        continue
                    pixmap = document.load_page(page - 1).get_pixmap(dpi=200)
                    out = target / f"AUDIT-{record.question_id}__{doc_id}__p{page}.png"
                    pixmap.save(out)
                    typer.echo(f"  rendered {out.name}")
        except (ParsingError, RuntimeError) as exc:  # pragma: no cover - corrupt PDF
            typer.echo(f"  {record.question_id}: cannot render: {exc}")


def _report_composition(path: Path) -> None:
    records = load_gold(path.read_text(encoding="utf-8").splitlines())
    counts = composition(records)
    typer.echo("")
    typer.echo("composition (this is what the report must print):")
    for key, value in counts.items():
        typer.echo(f"  {key:<24} {value}")
    drafted = counts["model_drafted"]
    if drafted:
        rate = counts["model_drafted_audited"] / drafted
        typer.echo(f"  {'audit rate':<24} {rate:.0%}")


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
