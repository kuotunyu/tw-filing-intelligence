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
is a set of values *plus* the year and series each belongs to. Two of those three are
checkable: the value must be labelled inside the cited crop (a bbox aimed at the wrong chart
on the same page fails), and it must sit on the row of the year the answer assigns it to.
What is not checkable is the *series*: a row of the capacity chart carries both 9% and
15-16, and only the legend's colours say which is the growth rate. So chart records report
as partly corroborated, and are force-included in the audit sample.

The first version of this claimed year attribution was unverifiable too, because "the CJK
on these pages comes out of the extractor as mojibake". It does not. That mojibake was this
tool's own stdout being encoded as cp950 -- the bug `twfi.console` was added to fix -- and
it corrupted the diagnosis before it corrupted any output. The year labels, legend text and
titles are all in the text layer. A wrong belief about the data had been written into a
check as a reason not to look.
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
            f"{partial} chart answer(s) partly corroborated: each value is labelled inside "
            "the cited crop\nand sits on the row of the year it is assigned to. What no text "
            "check can settle is\nwhich of a row's values belongs to which series -- that is "
            "in the legend colours. Those\nrecords are force-included in the audit sample."
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
    pairs = _chart_pairs(record.answer or "")
    if not pairs:
        return "unverifiable", "no year/value pairs to check"

    words, problem = _words_in_crop(record, lock, root)
    if problem is not None:
        return "unverifiable", problem

    rows = _rows_by_year(words)
    if not rows:
        return "unverifiable", "the cited crop carries no year labels to attribute values to"

    missing: list[str] = []
    for year, value in pairs:
        on_row = rows.get(year)
        if on_row is None:
            missing.append(f"{year}: no such label in the crop")
        elif value not in on_row:
            missing.append(f"{year}: {value} is not on that row (row has {sorted(on_row)})")
    if missing:
        return "absent", f"{len(missing)} of {len(pairs)} pair(s) wrong: {missing}"
    return "partial", (
        f"all {len(pairs)} year/value pair(s) sit on the right row of the cited crop; "
        "which of a row's values belongs to which series is only in the legend colours"
    )


def _chart_pairs(answer: str) -> list[tuple[str, str]]:
    """The (year label, value) pairs a chart answer claims.

    Checking values as an unordered multiset was the weaker earlier version of this: it
    accepted 「民國111年 6%；民國112年 9%」 for a chart that says the opposite, because both
    values are somewhere in the crop. The year is right there in the answer, so the pairing
    is checkable and the loose version was leaving a real error class through.
    """
    flat = _normalise_text(answer)
    pairs: list[tuple[str, str]] = []
    for match in _ERA_YEAR.finditer(flat):
        tail = flat[match.end() : match.end() + 24]
        value = _CHART_VALUE.search(tail)
        if value is not None:
            pairs.append((match.group(), value.group()))
    return pairs


#: Half the row pitch on the pages this runs against. Measured, not guessed: the capacity
#: chart's year labels sit at vertical centres 103.6 / 133.3 / 163.0, so the pitch is ~29.7
#: and a value's centre is within ~5.5 of its own year's. Twelve points separates rows with
#: room to spare while staying well under the pitch.
_ROW_BAND = 12.0


def _rows_by_year(
    words: list[tuple[float, float, float, float, str]],
) -> dict[str, set[str]]:
    """Map each era-year label in the crop to the values printed on its row.

    This exists because of a mistake worth naming: the first version of this check declared
    year attribution unverifiable, on the grounds that these pages' CJK came out of the
    extractor as mojibake. It does not. The mojibake was this tool's own stdout being
    encoded as cp950 -- the very bug `twfi.console` was added to fix -- and it corrupted the
    diagnosis before it corrupted the output. The year labels, the legend text and the
    titles are all in the text layer, so the pairing can be checked and now is.
    """
    years = [w for w in words if _ERA_YEAR.fullmatch(w[4])]
    rows: dict[str, set[str]] = {}
    for word in years:
        centre = (word[1] + word[3]) / 2
        rows.setdefault(word[4], set()).update(
            other[4]
            for other in words
            if _CHART_VALUE.fullmatch(other[4])
            and abs((other[1] + other[3]) / 2 - centre) <= _ROW_BAND
        )
    return rows


def _words_in_crop(
    record: GoldRecord, lock: object, root: Path
) -> tuple[list[tuple[float, float, float, float, str]], str | None]:
    """Every text label lying inside any of the record's cited bboxes, with its box.

    Word coordinates rather than the baseline parser's blocks: on these pages the baseline
    returns one block covering the whole sheet, so block containment could not tell the
    left-hand chart from the right-hand one, and telling them apart is most of the value
    here.
    """
    import pymupdf

    wanted: dict[int, list[tuple[float, float, float, float]]] = {}
    for ref in record.bbox:
        wanted.setdefault(ref.page, []).append(ref.bbox)

    # A chart record cites one filing; if that ever changes, every source is searched and
    # the labels are pooled, which is the same rule the prose branch already uses.
    found: list[tuple[float, float, float, float, str]] = []
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
                        box = (
                            float(word[0]),
                            float(word[1]),
                            float(word[2]),
                            float(word[3]),
                        )
                        if any(_inside(box, target) for target in boxes):
                            found.append((*box, _normalise_text(word[4])))
        except (ParsingError, RuntimeError) as exc:  # pragma: no cover - corrupt PDF
            return [], f"{doc_id} unreadable: {exc}"
    if not found:
        return [], "the cited crop contains no text labels at all"
    return found, None


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
