"""Pick a reproducible sample of model-drafted gold for a person to check.

    uv run python scripts/audit_gold.py --set locked            # show the sample
    uv run python scripts/audit_gold.py --set locked --render   # and render its pages
    uv run python scripts/audit_gold.py --set locked --accept LOCK-0021 --accept LOCK-0024
    uv run python scripts/audit_gold.py --set locked --reject LOCK-0022

`--accept` and `--reject` repeat per id. This line used to be written
`--accept LOCK-0021 LOCK-0024`, which typer rejects as an extra argument -- and the
usage text is what anyone reads before running it, so a wrong example is a broken tool.

Gold may be drafted by a model reading rendered page images (D-019). The risk that
introduces is not bad transcription -- a machine is better at digits than a person, as
PROBE-0004 showed -- but bad *question selection*: a drafter that also chooses the
questions can drift toward what the pipeline handles well, and nothing in the record
would reveal it.

The sampling rule -- seeded draw, plus types that are always audited rather than sampled --
lives in `twfi.eval.audit`, where it is tested. This file is the CLI over it.

`--accept` marks records audited. `--reject` clears the flag and is meant to be followed
by redrafting the whole question type, not just the one record: a drafting habit that
produced one bad question probably produced others.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from twfi.console import use_utf8_output
from twfi.eval.audit import AUDIT_SEED, DEFAULT_SAMPLE, audit_sample
from twfi.eval.gold import GoldRecord, GoldSet, composition, load_gold
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
    typer.echo("a reader would actually ask. Then, one --accept per id:")
    # Spelled out with the ids actually pending, because the placeholder version of this
    # line read as though several ids could follow one --accept, and they cannot.
    pending = [record.question_id for record in sample if not record.audited]
    flags = " ".join(f"--accept {qid}" for qid in pending) or "--accept <id> --accept <id>"
    typer.echo(f"  uv run python scripts/audit_gold.py --set {gold_set} {flags}")
    if render:
        typer.echo("")
        typer.echo(f"  images: {(paths.runs / 'pages').relative_to(paths.root)}")
        typer.echo("  a record citing a bbox also gets a __crop<n>.png -- read that one")
    _report_composition(path)


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
        for doc_id, wanted in _pages_by_document(record).items():
            acquired = lock.get(doc_id)
            if acquired is None or not acquired.local_path(paths.root).is_file():
                typer.echo(f"  {record.question_id}: {doc_id} not acquired")
                continue
            try:
                with pymupdf.open(acquired.local_path(paths.root)) as document:  # type: ignore[no-untyped-call]
                    for page in wanted:
                        if not 1 <= page <= document.page_count:
                            continue
                        loaded = document.load_page(page - 1)
                        pixmap = loaded.get_pixmap(dpi=200)
                        out = target / f"AUDIT-{record.question_id}__{doc_id}__p{page}.png"
                        pixmap.save(out)
                        typer.echo(f"  rendered {out.name}")
                        # A chart occupies a fifth of a spread; at whole-page scale its
                        # labels are too small to check, and the crop is what the record
                        # actually cites. Render it too, at a resolution that can be read.
                        for index, ref in enumerate(
                            [ref for ref in record.bbox if ref.page == page], start=1
                        ):
                            clip = pymupdf.Rect(*ref.bbox)  # type: ignore[no-untyped-call]
                            crop = target / (
                                f"AUDIT-{record.question_id}__{doc_id}__p{page}__crop{index}.png"
                            )
                            loaded.get_pixmap(dpi=300, clip=clip).save(crop)
                            typer.echo(f"  rendered {crop.name}")
            except (ParsingError, RuntimeError) as exc:  # pragma: no cover - corrupt PDF
                typer.echo(f"  {record.question_id}: cannot render: {exc}")


def _pages_by_document(record: GoldRecord) -> dict[str, list[int]]:
    """Which pages belong to which filing.

    ``page_numbers`` is a flat list, so a cross_document record's pages carry no hint of
    which of its two filings each came from. Assuming the first one rendered
    2330-FY2023-AR p.55 for a page that lives in 2330-FY2024-FS -- the same
    only-the-first-document mistake the corroboration check had.

    The information is already in the record: every ``required_evidence`` ref is written
    ``<doc_id>#p<page>``. Falling back to the flat list only when a record has a single
    filing, where there is nothing to get wrong.
    """
    found: dict[str, list[int]] = {}
    for item in record.required_evidence:
        doc_id, _, tail = item.ref.partition("#p")
        page = tail.split("/")[0]
        if doc_id in record.source_document and page.isdigit():
            found.setdefault(doc_id, []).append(int(page))
    if found:
        return {doc: sorted(set(pages)) for doc, pages in found.items()}
    return {record.source_document[0]: list(record.page_numbers)}


def _report_composition(path: Path) -> None:
    records = load_gold(path.read_text(encoding="utf-8").splitlines())
    counts = composition(records)
    typer.echo("")
    typer.echo("composition (this is what the report must print):")
    for key, value in counts.items():
        typer.echo(f"  {key:<24} {value}")
    eligible = counts["needs_audit"]
    if eligible:
        typer.echo(f"  {'audit rate':<24} {counts['audited'] / eligible:.0%}")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
