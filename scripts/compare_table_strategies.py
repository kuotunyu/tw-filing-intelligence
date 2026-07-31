"""Decide the table strategy by whether it recovers figures we already know are right.

    uv run python scripts/compare_table_strategies.py

`TableConfig` uses pdfplumber's ``text`` strategy for both axes. That misses tables drawn
with rectangles rather than lines: 1301-FY2023-AR p188 is a ruled 13x5 comparison table with
101 rects and one line, and the text strategy returns a 41x2 smear that the shape filter
correctly rejects. The ``lines`` strategy extracts it exactly, header and all.

A detection count was the first thing measured and it is the wrong measure. Over a 1-in-4
page sample of the eight usable filings, ``text`` found a table-shaped candidate on 298 pages
and ``lines`` on 175, with 59 pages only ``lines`` could see and 182 only ``text`` could.
Those are counts of *the filter passing*, not of the cells being right -- the same proxy
mistake D-020 was made of, where a figure-detector was judged by how much it removed rather
than by whether the survivors were figures.

So this compares them against figures whose correctness is already established: the gold
sets. Every gold record that cites a page and answers with a figure is a labelled example --
the answer was read off rendered pixels by a person or audited by one, never by an extractor
(D-016), so using it to grade extractors is not circular.

For each such record the question is narrow and objective: does the strategy produce a table
on the cited page containing that figure in a cell? Reported per strategy, plus what their
union would add. No page is hand-checked, nothing is judged by eye, and the answer does not
depend on anyone's taste.

Writes `results/runs/table_strategy_<set>.json`, named by set so a dev-basis measurement and
a locked held-out check can never be mistaken for each other.

Measured (dev, 15 known figures): ``text`` recovers **0**, ``lines`` recovers 9. The current
configuration cannot extract a single one of dev's known table figures. See D-027.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

import pdfplumber
import typer

from twfi.console import use_utf8_output
from twfi.eval.gold import GoldRecord, load_gold
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.normalise import normalise
from twfi.parsing.tables import TableConfig, is_table_like
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

STRATEGIES: dict[str, dict[str, str]] = {
    "text": {"vertical_strategy": "text", "horizontal_strategy": "text"},
    "lines": {"vertical_strategy": "lines", "horizontal_strategy": "lines"},
}

#: A figure as filings print it: at least four digits so that a year or a footnote marker is
#: not mistaken for a value the extractor was supposed to find.
_FIGURE = re.compile(r"\d[\d,]{3,}(?:\.\d+)?")
_SEPARATORS = re.compile(r"[,\s]")


@dataclass(frozen=True, slots=True)
class Target:
    """One figure whose correct value is already known, and where it should be found."""

    question_id: str
    doc_id: str
    page: int
    figure: str

    @property
    def bare(self) -> str:
        return _SEPARATORS.sub("", self.figure)


def targets_from(records: Sequence[GoldRecord]) -> list[Target]:
    """Every (page, figure) pair a gold record asserts.

    ``derived_from`` operands count and the derived answer does not: a growth rate is not
    printed in any cell, so looking for it would mark every strategy as failing on a page
    where both are in fact fine.
    """
    out: list[Target] = []
    for record in records:
        sources = record.derived_from or ((record.answer,) if record.answer else ())
        figures = {
            match.group() for text in sources if text for match in _FIGURE.finditer(str(text))
        }
        # A record citing several pages does not say which figure is on which, so every
        # figure is looked for on every cited page and a hit on any of them counts.
        for figure in sorted(figures):
            for page in record.page_numbers:
                out.append(
                    Target(
                        question_id=record.question_id,
                        doc_id=record.source_document[0]
                        if len(record.source_document) == 1
                        else "",
                        page=page,
                        figure=figure,
                    )
                )
    return out


def cells_on(page: Any, settings: dict[str, str], config: TableConfig) -> list[str]:
    """Every cell of every table-shaped candidate the strategy finds on one page."""
    # pdfplumber raises assorted things on unusual geometry -- IndexError and ValueError from
    # its own edge intersection, TypeError on a None bbox. A strategy that crashes on a page
    # has failed on that page, which is the answer this is collecting, so it is caught and
    # counted as "found nothing" rather than aborting the comparison. Deliberately broad:
    # enumerating pdfplumber's internal exception types would make the measurement depend on
    # its implementation details.
    cells: list[str] = []
    try:
        candidates = page.find_tables(settings)
    except Exception:
        return cells
    for candidate in candidates:
        try:
            rows = tuple(
                tuple(normalise(cell or "").strip() for cell in row) for row in candidate.extract()
            )
        except Exception:  # noqa: S112 - a crash on this page IS the datum being collected
            continue
        if not is_table_like(rows, config):
            continue
        cells.extend(cell for row in rows for cell in row if cell)
    return cells


def found(figure: Target, cells: Iterable[str]) -> bool:
    """Whether a cell carries the figure, with or without thousands separators."""
    for cell in cells:
        if figure.figure in cell:
            return True
        if figure.bare and figure.bare in _SEPARATORS.sub("", cell):
            return True
    return False


@app.command()
def main(
    limit: Annotated[int, typer.Option(help="Cap the number of targets (0 = all).")] = 0,
    gold_set: Annotated[
        str,
        typer.Option(
            "--set",
            help="dev (the only set a tuning decision may rest on), locked, or all.",
        ),
    ] = "dev",
) -> None:
    """Grade each table strategy against gold figures.

    Defaults to **dev**, and that default is the point. Protocol 1.3 allows thresholds and
    parser settings to be tuned on the development split only. Grading strategies against
    locked figures and then changing the extractor would be tuning on the locked set with
    extra steps -- the first run of this script did exactly that, over 48 figures pooled from
    every set, before the violation was noticed. ``--set locked`` exists so the same numbers
    can be reported afterwards as a held-out check, never as the basis for the choice.
    """
    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    config = TableConfig()

    if gold_set not in {"dev", "locked", "all"}:
        typer.echo(f"unknown set {gold_set!r}; choose dev, locked or all")
        raise typer.Exit(code=2)
    sources = {
        "dev": (paths.dev_gold,),
        "locked": (paths.locked_gold, paths.locked_probes),
        "all": (paths.dev_gold, paths.locked_gold, paths.locked_probes),
    }[gold_set]
    typer.echo(f"grading against the {gold_set} set")
    if gold_set != "dev":
        typer.echo(
            "  NOTE: a tuning decision may not rest on this. Protocol 1.3 permits parser "
            "settings to be chosen on dev only; these numbers are a held-out check."
        )

    records: list[GoldRecord] = []
    for path in sources:
        if path.is_file():
            records.extend(load_gold(path.read_text(encoding="utf-8").splitlines()))

    wanted = [target for target in targets_from(records) if target.doc_id]
    if limit:
        wanted = wanted[:limit]
    typer.echo(f"{len(wanted)} (page, figure) target(s) from {len(records)} gold record(s)")
    if not wanted:
        typer.echo("no targets; annotate some gold first")
        raise typer.Exit(code=2)

    by_document: dict[str, list[Target]] = {}
    for target in wanted:
        by_document.setdefault(target.doc_id, []).append(target)

    results: list[dict[str, Any]] = []
    for doc_id, group in sorted(by_document.items()):
        acquired = lock.get(doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            typer.echo(f"{doc_id}: not acquired")
            continue
        pages = sorted({target.page for target in group})
        with pdfplumber.open(acquired.local_path(paths.root)) as document:
            cache: dict[tuple[int, str], list[str]] = {}
            for page_number in pages:
                if not 1 <= page_number <= len(document.pages):
                    continue
                page = document.pages[page_number - 1]
                for name, settings in STRATEGIES.items():
                    cache[(page_number, name)] = cells_on(page, settings, config)
            for target in group:
                hit = {
                    name: found(target, cache.get((target.page, name), [])) for name in STRATEGIES
                }
                results.append(
                    {
                        "question_id": target.question_id,
                        "doc_id": target.doc_id,
                        "page": target.page,
                        "figure": target.figure,
                        **{f"found_{name}": value for name, value in hit.items()},
                    }
                )
        typer.echo(f"  {doc_id}: {len(group)} target(s) over {len(pages)} page(s)")

    # A record may cite several pages for one figure; it is satisfied if any of them has it.
    per_figure: dict[tuple[str, str], dict[str, bool]] = {}
    for row in results:
        key = (str(row["question_id"]), str(row["figure"]))
        current = per_figure.setdefault(key, {name: False for name in STRATEGIES})
        for name in STRATEGIES:
            current[name] = current[name] or bool(row[f"found_{name}"])

    counts = {name: sum(1 for v in per_figure.values() if v[name]) for name in STRATEGIES}
    union = sum(1 for v in per_figure.values() if any(v.values()))
    neither = sum(1 for v in per_figure.values() if not any(v.values()))
    only = {
        name: sum(
            1
            for v in per_figure.values()
            if v[name] and not any(v[other] for other in STRATEGIES if other != name)
        )
        for name in STRATEGIES
    }
    total = len(per_figure)

    typer.echo("")
    typer.echo(f"figures graded: {total}")
    for name in STRATEGIES:
        typer.echo(f"  {name:<6} recovers {counts[name]:>3}/{total}  ({counts[name] / total:.0%})")
        typer.echo(f"         of which only {name}: {only[name]}")
    typer.echo(f"  union  recovers {union:>3}/{total}  ({union / total:.0%})")
    typer.echo(f"  neither strategy recovers {neither}")
    typer.echo("")
    typer.echo("A figure neither strategy finds is not necessarily an extractor failure: the")
    typer.echo("page may hold it in prose rather than a table, and 2330-FY2024-FS pp.7-15 have")
    typer.echo("no text layer at all (D-017). Those are listed so they can be told apart.")

    payload = {
        "gold_set": gold_set,
        "figures_graded": total,
        "recovered": counts,
        "only": only,
        "union": union,
        "neither": neither,
        "missed_by_both": [
            {"question_id": qid, "figure": figure}
            for (qid, figure), hit in sorted(per_figure.items())
            if not any(hit.values())
        ],
        "rows": results,
        "note": (
            "Graded against gold figures, which are read from rendered pixels or audited by a "
            "person and never produced by an extractor (D-016), so this is not circular."
        ),
    }
    destination = paths.runs / f"table_strategy_{gold_set}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    typer.echo("")
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
