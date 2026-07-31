"""Merge hand-written gold answers into valid JSONL, so nobody edits JSON by hand.

    uv run python scripts/fill_gold.py --set probe --skeleton   # write the form
    uv run python scripts/fill_gold.py --set probe              # merge and validate

The mechanical fields of a gold record are long and JSON is unforgiving: a missing quote
in a text editor produces "the file is not valid JSON", which says nothing about which
figure was mistyped. So the annotator fills in a flat form with two lines per probe, and
this script writes the JSON.

The form carries only what a person must decide -- the question, the answer, and the unit
if the page disagrees with what was assumed. Everything else comes from
`probes.template.jsonl` and cannot be broken by editing the form.

Answers are still read by a human off a rendered page (`scripts/render_pages.py`); this
script only moves text, and refuses to invent any.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.eval.gold import load_gold, set_problems
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

_ID = re.compile(r"^\s*((?:PROBE|LOCK|DEV|CHAL)-\d{4})\s*$")
_FIELD = re.compile(r"^\s*(Q|A|UNIT|CURRENCY)\s*:\s*(.*)$", re.IGNORECASE)

SKELETON_HEADER = """\
# GOLD ANSWERS -- fill in Q and A for each block, then run:
#     uv run python scripts/fill_gold.py --set <set>
#
# Q: the question, in your own words.
# A: the figure exactly as printed on the page, digits and separators only.
#    Do not write the unit here -- the unit has its own line.
# UNIT / CURRENCY: only change these if the page disagrees with the value shown.
#
# Read the figure off the rendered page image, never off extracted text: the extractor
# is the thing under test, and a digit it misreads would become a wrong gold answer the
# candidate reproduces and is scored correct for (D-016).
#
#     uv run python scripts/render_pages.py --from-probes
#
# Lines starting with # are ignored. Blank Q or A lines are reported, not accepted.
"""


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="Which set: probe, locked, dev, challenger.")
    ] = "probe",
    skeleton: Annotated[
        bool, typer.Option("--skeleton", help="Write the form to fill in, then stop.")
    ] = False,
    form: Annotated[
        Path | None, typer.Option(help="Where the form lives. Defaults next to probes.jsonl.")
    ] = None,
) -> None:
    """Write the answer form, or merge a filled-in one into probes.jsonl."""
    paths = repo_paths()
    targets = {
        "probe": paths.locked_probes,
        "locked": paths.locked_gold,
        "dev": paths.dev_gold,
        "challenger": paths.chart_challenger,
    }
    if gold_set not in targets:
        typer.echo(f"unknown set {gold_set!r}; choose from {sorted(targets)}")
        raise typer.Exit(code=2)
    target = targets[gold_set]
    template = target.with_suffix(".template.jsonl")
    form_path = form or target.with_name(f"{target.stem}.answers.txt")

    if not template.is_file():
        typer.echo(f"missing template: {template}")
        raise typer.Exit(code=2)
    records = _raw_records(template)

    if skeleton:
        # --skeleton once destroyed a filled-in form, and the answers in it had taken an
        # hour to collect. Overwriting work is not something a convenience flag should be
        # able to do by accident.
        if form_path.is_file() and _has_answers(form_path.read_text(encoding="utf-8")):
            typer.echo(f"{form_path.relative_to(paths.root)} already has answers in it.")
            typer.echo("Refusing to overwrite. Delete it first if that is really the intent,")
            typer.echo("or edit it by hand to add the new blocks.")
            raise typer.Exit(code=2)
        form_path.write_text(_skeleton(records), encoding="utf-8")
        typer.echo(f"wrote the form: {form_path.relative_to(paths.root)}")
        typer.echo("")
        for record in records:
            pages = ", ".join(f"p{page}" for page in record.get("page_numbers", ()))
            typer.echo(
                f"  {record['question_id']}  {record['company']['name']} "
                f"{record['period']}  {record['source_document'][0]} {pages}"
            )
        typer.echo("")
        typer.echo("Fill in Q and A for each block, then run this script again.")
        return

    if not form_path.is_file():
        typer.echo(f"no form at {form_path.relative_to(paths.root)} -- run with --skeleton first")
        raise typer.Exit(code=2)

    # An unanswerable question has no answer to write, so the form must not demand one.
    unanswerable = {
        str(record["question_id"]) for record in records if record.get("answerable") is False
    }
    answers, problems = _parse_form(form_path.read_text(encoding="utf-8"), unanswerable)
    known = {str(record["question_id"]) for record in records}
    problems.extend(f"{qid}: not in the template" for qid in sorted(set(answers) - known))
    for qid in sorted(known - set(answers)):
        problems.append(f"{qid}: no block in the form")

    if problems:
        typer.echo("FORM PROBLEMS:")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)

    merged = [_merge(record, answers[str(record["question_id"])]) for record in records]
    target.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in merged),
        encoding="utf-8",
    )
    typer.echo(f"wrote {len(merged)} record(s): {target.relative_to(paths.root)}")

    loaded = load_gold(target.read_text(encoding="utf-8").splitlines())
    remaining = set_problems(loaded, gold_set=gold_set)  # type: ignore[arg-type]
    typer.echo("")
    if remaining:
        typer.echo("VALIDATION PROBLEMS:")
        for problem in remaining:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)
    typer.echo(f"all {len(loaded)} {gold_set} record(s) valid.")
    # Not `record`: that name is already bound to a raw dict above, and rebinding it to a
    # GoldRecord is how one function ends up with two types under one name.
    for probe in loaded:
        unit = f" {probe.unit}" if probe.unit else ""
        typer.echo(f"  {probe.question_id}  {probe.answer}{unit}  <- {probe.question}")
    typer.echo("")
    typer.echo("Next: uv run python scripts/check_leakage.py")


@dataclass(slots=True)
class _Answer:
    """The only four things the form lets a person change."""

    question: str = ""
    answer: str = ""
    unit: str | None = None
    currency: str | None = None


def _has_answers(text: str) -> bool:
    """Whether a form carries at least one filled-in answer."""
    return any(
        line.strip().upper().startswith("A:") and line.split(":", 1)[1].strip()
        for line in text.splitlines()
    )


def _raw_records(template: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in template.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("//"):
            records.append(json.loads(stripped))
    return records


def _skeleton(records: list[dict[str, Any]]) -> str:
    parts = [SKELETON_HEADER]
    for record in records:
        pages = ", ".join(f"p{page}" for page in record.get("page_numbers", ()))
        # Split on the sentence break, not on any period: the notes contain "p.55".
        note = re.split(r"\.\s", str(record.get("annotation_notes", "")))[0]
        parts.append(
            f"\n# --- {record['company']['name']} {record['period']} :: "
            f"{record['source_document'][0]} {pages} :: {note}\n"
            f"{record['question_id']}\n"
            f"Q: \n"
            f"A: \n"
            f"UNIT: {record.get('unit') or ''}\n"
        )
    return "".join(parts)


def _parse_form(
    text: str, unanswerable: set[str] | None = None
) -> tuple[dict[str, _Answer], list[str]]:
    answers: dict[str, _Answer] = {}
    problems: list[str] = []
    current: str | None = None

    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        header = _ID.match(line)
        if header:
            current = header.group(1)
            answers.setdefault(current, _Answer())
            continue
        field = _FIELD.match(line)
        if not field:
            problems.append(f"line {number}: expected '<ID>-nnnn', 'Q:', 'A:' or 'UNIT:'")
            continue
        if current is None:
            problems.append(f"line {number}: a field before any id header")
            continue
        key, value = field.group(1).upper(), field.group(2).strip()
        entry = answers[current]
        if key == "Q":
            entry.question = value
        elif key == "A":
            entry.answer = value
        elif key == "UNIT":
            entry.unit = value or None
        else:
            entry.currency = value or None

    skip = unanswerable or set()
    for qid, entry in sorted(answers.items()):
        if not entry.question:
            problems.append(f"{qid}: Q is empty -- write the question you want asked")
        if not entry.answer and qid not in skip:
            problems.append(f"{qid}: A is empty -- read the figure off the rendered page")
    return answers, problems


def _merge(record: dict[str, Any], answer: _Answer) -> dict[str, Any]:
    merged = dict(record)
    merged["question"] = answer.question
    if record.get("answerable") is False:
        # Whatever the form says, an unanswerable question's answer is null. The point of
        # the record is that there is nothing to write, and a placeholder string would be
        # a stated answer to a question the filings cannot answer.
        merged["answer"] = None
        return merged
    merged["answer"] = answer.answer
    if answer.unit is not None:
        merged["unit"] = answer.unit
    if answer.currency is not None:
        merged["currency"] = answer.currency
    return merged


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
