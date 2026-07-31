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

A fourth outcome exists for chart records, and it is deliberately not "ok". A chart answer
is a set of values *plus* the year and series each belongs to. The values are checkable:
they must appear as labels inside the cited crop, and a bbox pointing at the wrong chart on
the same page fails that. The attribution is not checkable, because the thing that carries
it -- an axis label, a legend colour -- is exactly what the text layer loses. So those
records are reported as partly corroborated and are force-included in the audit sample,
never counted as verified.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer

from twfi.console import use_utf8_output
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

#: A prose answer is checked atom by atom, not as one string. Demanding a verbatim quote
#: fails on things that are plainly right: the page writes 「…日本熊本縣以及德國德勒斯登」
#: while the answer joins with 、, wraps names in 「」, and writes 民國114年1月1日 where the
#: page says 「114 年 1 月 1 日」. Those are the same facts.
#:
#: Atoms, not clauses. A composite answer like
#: 「資產總額 5,532,371,215 千元；營業毛利 1,175,110,628 千元」 pairs labels with figures
#: from different table cells, so no clause of it appears contiguously anywhere -- while
#: the labels and the figures each do. The sentence joining them is the annotator's.
#:
#: The comma is deliberately not a separator: it is the thousands separator, and splitting
#: on it shredded every figure into 資產總額5 / 215千元 and called them all missing.
_FIGURE_ATOM = re.compile(r"\d[\d,]{2,}(?:\.\d+)?")
_CJK_ATOM = re.compile(r"[\u4e00-\u9fff]{2,}")
#: Dropped before comparing: era names a filing states inconsistently, and words that are
#: the annotator's connective tissue rather than anything a page must contain.
_ERA_PREFIX = re.compile(r"^(?:中華民國|民國)")
_CONNECTIVES = frozenset(
    {"分別", "是多少", "以及", "單位", "單位為", "金額", "經董事會", "通過發布", "增加為"}
)
#: 仟 and 千 are the same unit written two ways; a filing uses one and an answer the other.
_UNIT_ALIAS = str.maketrans({"仟": "千", "臺": "台"})

#: Stripped from a chart answer before its values are read out. 民國111年 is an axis label,
#: and on these pages the axis labels are CJK the extractor renders as mojibake -- looking
#: for "111" would fail on a correct answer.
_ERA_YEAR = re.compile(r"(?:中華民國|民國)?\d{2,3}年(?:度)?")
#: A chart value as the chart prints it: a number, a percentage, or a forecast range.
_CHART_VALUE = re.compile(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?%?")
#: Half a point, so a label sitting exactly on the crop edge is inside it.
_CROP_TOLERANCE = 0.5


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
    partial = 0
    checked = 0
    marks = {"cited": "ok  ", "unverifiable": "?   ", "partial": "~   "}

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
            typer.echo(
                f"  {marks.get(verdict, '!!  ')}{record.question_id}  {record.answer:>18}  {detail}"
            )
            if verdict in {"elsewhere", "absent"}:
                problems += 1
            elif verdict == "partial":
                partial += 1

    typer.echo("")
    if problems:
        typer.echo(f"FAILED: {problems} of {checked} answer(s) not corroborated on the cited page")
        raise typer.Exit(code=1)
    typer.echo(f"{checked} answer(s) corroborated, or on a page with no text layer to check")
    if partial:
        typer.echo("")
        typer.echo(
            f"{partial} chart answer(s) only partly corroborated: the values are inside the "
            "cited crop,\nbut which year and which series each belongs to survives only in "
            "the pixels. Those\nrecords are force-included in the audit sample -- a person "
            "has to look at the chart."
        )


def _verify(
    record: GoldRecord,
    lock: object,
    root: Path,
    cache: dict[str, dict[int, str]],
) -> tuple[str, str]:
    if record.bbox and "chart_crop" in record.evidence_kinds:
        return _verify_crop(record, lock, root)

    # A cross_document answer cites two filings. Loading only the first reported every
    # atom from the second as missing, which looked like a wrong answer and was a wrong
    # reader.
    merged: dict[int, str] = {}
    for doc in record.source_document:
        loaded, problem = _pages_of(doc, lock, root, cache)
        if problem is not None:
            return "unverifiable", problem
        for page, text in loaded.items():
            merged[page] = merged.get(page, "") + text
    return _verify_against(record, merged, ", ".join(record.source_document))


def _verify_crop(record: GoldRecord, lock: object, root: Path) -> tuple[str, str]:
    """Check a chart answer's values against the labels inside the cited crop.

    Word coordinates, not the baseline parser's blocks: on these pages the baseline
    returns one block covering the whole sheet, so block containment cannot tell the
    left-hand chart from the right-hand one. Distinguishing them is the entire value of
    this check -- a bbox aimed at the wrong chart on the right page is the mistake D-020
    was made of, and only coordinates catch it.
    """
    values = _chart_values(record.answer or "")
    if not values:
        return "unverifiable", "no chart values to check"

    labels, problem = _labels_in_crop(record, lock, root)
    if problem is not None:
        return "unverifiable", problem

    # A multiset, because 「民國112年 6%；民國113年 6%」 needs two 6% labels in the crop.
    # Collapsing to a set would let one label stand in for a year that has none.
    remaining = list(labels)
    missing: list[str] = []
    for value in values:
        if value in remaining:
            remaining.remove(value)
        else:
            missing.append(value)
    if missing:
        return "absent", (
            f"{len(missing)} of {len(values)} value(s) not labelled inside the cited crop: "
            f"{missing}"
        )
    return "partial", (
        f"all {len(values)} value(s) labelled inside the cited crop; year and series "
        "attribution is not in the text layer"
    )


def _chart_values(answer: str) -> list[str]:
    """The values a chart answer claims, without the axis labels naming the years."""
    stripped = _ERA_YEAR.sub(" ", _normalise_text(answer))
    return [match.group() for match in _CHART_VALUE.finditer(stripped)]


def _labels_in_crop(record: GoldRecord, lock: object, root: Path) -> tuple[list[str], str | None]:
    """Every text label lying inside any of the record's cited bboxes."""
    import pymupdf

    wanted: dict[int, list[tuple[float, float, float, float]]] = {}
    for ref in record.bbox:
        wanted.setdefault(ref.page, []).append(ref.bbox)

    # A chart record cites one filing; if that ever changes, every source is searched and
    # the labels are pooled, which is the same rule the prose branch already uses.
    labels: list[str] = []
    for doc_id in record.source_document:
        acquired = lock.get(doc_id)  # type: ignore[attr-defined]
        if acquired is None or not acquired.local_path(root).is_file():
            return [], f"{doc_id} not acquired"
        try:
            with pymupdf.open(acquired.local_path(root)) as document:  # type: ignore[no-untyped-call]
                for page, boxes in wanted.items():
                    if not 1 <= page <= document.page_count:
                        continue
                    for word in document.load_page(page - 1).get_text("words"):
                        x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
                        if any(_inside((x0, y0, x1, y1), box) for box in boxes):
                            labels.append(_normalise_text(text))
        except (ParsingError, RuntimeError) as exc:  # pragma: no cover - corrupt PDF
            return [], f"{doc_id} unreadable: {exc}"
    if not labels:
        return [], "the cited crop contains no text labels at all"
    return labels, None


def _inside(
    word: tuple[float, float, float, float], box: tuple[float, float, float, float]
) -> bool:
    return (
        word[0] >= box[0] - _CROP_TOLERANCE
        and word[1] >= box[1] - _CROP_TOLERANCE
        and word[2] <= box[2] + _CROP_TOLERANCE
        and word[3] <= box[3] + _CROP_TOLERANCE
    )


def _pages_of(
    doc_id: str, lock: object, root: Path, cache: dict[str, dict[int, str]]
) -> tuple[dict[int, str], str | None]:
    """Every page of one filing as flattened text, cached across records."""
    pages = cache.get(doc_id)
    if pages is None:
        acquired = lock.get(doc_id)  # type: ignore[attr-defined]
        if acquired is None or not acquired.local_path(root).is_file():
            return {}, f"{doc_id} not acquired"
        try:
            parsed = parse_baseline(acquired.local_path(root), doc_id)
        except ParsingError as exc:
            return {}, f"{doc_id} unreadable: {exc}"
        by_page: dict[int, list[str]] = {}
        for block in parsed.blocks:
            by_page.setdefault(block.page, []).append(block.text)
        pages = {page: _WHITESPACE.sub("", "\n".join(text)) for page, text in by_page.items()}
        cache[doc_id] = pages
    return pages, None


def _verify_against(record: GoldRecord, pages: dict[int, str], doc_id: str) -> tuple[str, str]:
    """Judge one record against already-loaded page text."""

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


def _normalise_text(text: str) -> str:
    return _WHITESPACE.sub("", text).translate(_UNIT_ALIAS)


def _parts(answer: str) -> list[str]:
    """The atoms a cited page must contain: every figure, and every substantive term."""
    flat = _normalise_text(answer)
    atoms = [atom for atom in _FIGURE_ATOM.findall(flat) if len(atom) >= 3]
    for term in _CJK_ATOM.findall(flat):
        cleaned = _ERA_PREFIX.sub("", term)
        if len(cleaned) >= 2 and cleaned not in _CONNECTIVES:
            atoms.append(cleaned)
    return atoms


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
    text = _normalise_text("".join(pages.get(page, "") for page in cited))
    if not text:
        return "unverifiable", f"cited page(s) {sorted(cited)} have no text layer"
    missing = [part for part in parts if part not in text]
    if missing:
        elsewhere = _normalise_text("".join(pages.values()))
        where = (
            " (present elsewhere, so the citation is wrong)"
            if all(part in elsewhere for part in missing)
            else f" (absent from {doc_id} entirely)"
        )
        return "absent", f"{len(missing)} of {len(parts)} atom(s) missing: {missing}{where}"
    return "cited", f"all {len(parts)} atom(s) on cited page(s) {sorted(cited)}"


def _appears(value: str, pages: dict[int, str], cited: tuple[int, ...]) -> bool:
    """Whether one figure appears on any cited page, with or without separators."""
    bare = _SEPARATORS.sub("", value)
    return any(
        value in pages.get(page, "") or (bare and bare in _SEPARATORS.sub("", pages.get(page, "")))
        for page in cited
    )


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
