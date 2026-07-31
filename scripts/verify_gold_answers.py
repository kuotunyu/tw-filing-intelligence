"""Check that each gold answer's digits appear on the page the record cites.

    uv run python scripts/verify_gold_answers.py
    uv run python scripts/verify_gold_answers.py --set locked

This is corroboration, not a source. The figure in a gold record was read by a person off
a rendered page; this re-reads the same page with the text extractor and asks whether the
same digits are there. Agreement is evidence. Disagreement is a flag to look again -- it
does not say who was wrong, because either side can be.

It exists because a transcription slip got into the probe set and survived every other
check. PROBE-0004 recorded 6,861,904,180 for 鴻海's FY2024 revenue, a figure that appears
nowhere in the filing; the page says 6,859,615,493. validate_gold passed it (the schema
was correct), check_leakage passed it (the split was correct), and the annotator's own
arithmetic check passed -- because the check was performed but not reported, so nobody
verified that it had been performed on the recorded number. The error surfaced only when
a later question sent the annotator back to the same page for a different row.

Three outcomes, deliberately distinguished:

* **on the cited page** -- corroborated.
* **elsewhere in the document** -- the figure is real but the citation points somewhere
  else, which fails gate G4 even though the answer is right.
* **not in the document** -- either a misread, or the page has no text layer. Those are
  different problems, so a page the extractor cannot read at all is reported as
  unverifiable rather than as wrong.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer

from twfi.errors import ParsingError
from twfi.eval.gold import GoldRecord, GoldSet, load_gold
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.baseline import parse_baseline
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

_WHITESPACE = re.compile(r"\s+")
#: Answers are recorded as printed, with separators; pages print them the same way. Both
#: sides are also compared without separators, so 1,465,427,753 matches 1465427753.
_SEPARATORS = re.compile(r"[,\s]")

#: A prose answer is checked item by item, not as one string. Demanding a verbatim quote
#: fails on things that are plainly right: the page writes
#: 「…日本熊本縣以及德國德勒斯登」 while the answer joins the list with 、, and it writes
#: 「114 年 1 月 1 日」 where the answer says 民國114年1月1日. Those are the same facts.
_LIST_SEPARATORS = re.compile(r"[、,，;；]|以及|及(?![期權])|和(?!解)")
#: Dropped before comparing, because a filing states the era name inconsistently.
_ERA_PREFIX = re.compile(r"^(?:中華民國|民國)")
#: Ignored as parts: quote marks and brackets the filing puts round names.
_DECORATION = re.compile(r"[「」『』（）()\[\]《》\s]")


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="Which set to check: probe, locked, dev, challenger.")
    ] = "all",
) -> None:
    """Re-read each cited page and report whether the recorded figure is on it."""
    paths = repo_paths()
    files: dict[GoldSet, Path] = {
        "probe": paths.locked_probes,
        "locked": paths.locked_gold,
        "dev": paths.dev_gold,
        "challenger": paths.chart_challenger,
    }
    if gold_set != "all":
        if gold_set not in files:
            typer.echo(f"unknown set {gold_set!r}")
            raise typer.Exit(code=2)
        files = {name: path for name, path in files.items() if name == gold_set}

    lock = load_acquisition_lock(paths.acquisition_lock)
    pages_cache: dict[str, dict[int, str]] = {}
    problems = 0
    checked = 0

    for name, path in files.items():
        if not path.is_file():
            continue
        records = load_gold(path.read_text(encoding="utf-8").splitlines())
        typer.echo(f"=== {name} ===")
        for record in records:
            if record.answer is None:
                continue
            checked += 1
            verdict, detail = _verify(record, lock, paths.root, pages_cache)
            mark = (
                "ok  " if verdict == "cited" else ("?   " if verdict == "unverifiable" else "!!  ")
            )
            typer.echo(f"  {mark}{record.question_id}  {record.answer:>18}  {detail}")
            if verdict in {"elsewhere", "absent"}:
                problems += 1

    typer.echo("")
    if problems:
        typer.echo(f"FAILED: {problems} of {checked} answer(s) not corroborated on the cited page")
        raise typer.Exit(code=1)
    typer.echo(f"{checked} answer(s) corroborated, or on a page with no text layer to check")


def _verify(
    record: GoldRecord,
    lock: object,
    root: Path,
    cache: dict[str, dict[int, str]],
) -> tuple[str, str]:
    doc_id = record.source_document[0]
    pages = cache.get(doc_id)
    if pages is None:
        acquired = lock.get(doc_id)  # type: ignore[attr-defined]
        if acquired is None or not acquired.local_path(root).is_file():
            return "unverifiable", f"{doc_id} not acquired"
        try:
            parsed = parse_baseline(acquired.local_path(root), doc_id)
        except ParsingError as exc:
            return "unverifiable", f"{doc_id} unreadable: {exc}"
        by_page: dict[int, list[str]] = {}
        for block in parsed.blocks:
            by_page.setdefault(block.page, []).append(block.text)
        pages = {page: _WHITESPACE.sub("", "\n".join(text)) for page, text in by_page.items()}
        cache[doc_id] = pages

    # A derived answer -- a growth rate, a difference -- is not printed anywhere. What
    # must be corroborated is the figures it was computed from.
    if record.is_derived:
        missing = [
            operand
            for operand in record.derived_from
            if not _appears(operand, pages, record.page_numbers)
        ]
        if missing:
            return "absent", f"operand(s) {missing} not on cited page(s)"
        return "cited", f"derived; {len(record.derived_from)} operand(s) on the cited page"

    answer = record.answer or ""
    cited = set(record.page_numbers)

    if record.question_type in {"narrative_fact", "cross_page", "cross_document"}:
        return _verify_prose(answer, pages, record.page_numbers, doc_id)

    bare = _SEPARATORS.sub("", answer)
    found = sorted(
        page
        for page, text in pages.items()
        if answer in text or (bare and bare in _SEPARATORS.sub("", text))
    )

    if set(found) & cited:
        return "cited", f"on cited page(s) {sorted(set(found) & cited)}"
    if found:
        return "elsewhere", f"found on {found[:5]} but record cites {sorted(cited)}"
    if all(not pages.get(page, "").strip() for page in cited):
        return "unverifiable", f"cited page(s) {sorted(cited)} have no text layer"
    return "absent", f"does not appear anywhere in {doc_id}"


def _parts(answer: str) -> list[str]:
    """The substantive items of a prose answer, stripped of decoration."""
    found = []
    for chunk in _LIST_SEPARATORS.split(answer):
        cleaned = _ERA_PREFIX.sub("", _DECORATION.sub("", chunk))
        if len(cleaned) >= 2:
            found.append(cleaned)
    return found


def _verify_prose(
    answer: str, pages: dict[int, str], cited: tuple[int, ...], doc_id: str
) -> tuple[str, str]:
    """Every item of a prose answer must appear on some cited page.

    Item by item rather than verbatim, because a correct answer is not a quotation: the
    filing writes lists with 以及 and wraps names in 「」, and an annotator writes them
    with 、 and without. Requiring the joined string flagged three answers that were right.
    """
    parts = _parts(answer)
    if not parts:
        return "unverifiable", "no substantive text to check"
    text = "".join(pages.get(page, "") for page in cited)
    if not text.strip():
        return "unverifiable", f"cited page(s) {sorted(cited)} have no text layer"
    missing = [part for part in parts if part not in text]
    if missing:
        elsewhere = "".join(pages.values())
        where = (
            " (but present elsewhere in the filing)"
            if all(part in elsewhere for part in missing)
            else f" (absent from {doc_id} entirely)"
        )
        return "absent", f"{len(missing)} of {len(parts)} item(s) missing: {missing}{where}"
    return "cited", f"all {len(parts)} item(s) on cited page(s) {sorted(cited)}"


def _appears(value: str, pages: dict[int, str], cited: tuple[int, ...]) -> bool:
    bare = _SEPARATORS.sub("", value)
    return any(
        value in pages.get(page, "") or (bare and bare in _SEPARATORS.sub("", pages.get(page, "")))
        for page in cited
    )


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
