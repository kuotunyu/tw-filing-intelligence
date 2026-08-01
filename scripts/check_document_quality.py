"""Check whether each acquired filing can actually serve as evidence.

    uv run python scripts/check_document_quality.py

Run this before annotating anything. A filing with an unreadable text layer or with
its financial statements in a different file would otherwise look like a retrieval
failure or a numeric-route failure, and the study would draw the wrong conclusion
from it.

Two different questions are asked about the text layer, and both are needed:

* ``readable`` is whether pages produced characters at all.
* ``garbled`` is whether those characters are the right ones. A page can be fully readable and
  entirely mojibake, and the readable ratio cannot tell -- `2412-FY2023-AR` and `1301-FY2023-AR`
  both measure 95-96% readable while 18% and 15% of their characters decode to the wrong script
  (:mod:`twfi.parsing.garbled`). Reporting only the first would say those two filings are fine.

Writes results/runs/document_quality.json. CPU only, no network.
"""

from __future__ import annotations

import json

import pymupdf
import typer

from twfi.console import use_utf8_output
from twfi.io.manifest import load_acquisition_lock, load_document_manifest
from twfi.parsing.garbled import DocumentDefects, document_defects
from twfi.parsing.normalise import normalise
from twfi.parsing.quality import assess_pages
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


def _page_texts(path: str) -> list[str]:
    with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
        return [str(document.load_page(index).get_text()) for index in range(document.page_count)]


@app.command()
def main() -> None:
    """Assess every acquired document and report which ones are usable."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    manifest = load_document_manifest(paths.documents_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    assessments = []
    defects: dict[str, DocumentDefects] = {}
    for record in manifest.documents:
        target = record.local_path(paths.root)
        if lock.get(record.doc_id) is None or not target.is_file():
            typer.echo(f"skip {record.doc_id}: not acquired")
            continue
        texts = _page_texts(str(target))
        assessments.append(assess_pages(record.doc_id, texts))
        # Normalised first, as every other consumer of this text does: NFC recovers the
        # compatibility ideographs that 1301-FY2023-AR uses for 年 and 度 (D-024), and counting
        # them as off-core would report a sound page as broken.
        defects[record.doc_id] = document_defects(
            record.doc_id, [(number, normalise(text)) for number, text in enumerate(texts, 1)]
        )

    if not assessments:
        typer.echo("nothing acquired yet")
        raise typer.Exit(code=1)

    header = (
        f"{'doc_id':<18}{'pages':>6}{'chars/pg':>10}{'readable':>10}"
        f"{'garbled':>9}{'pages!':>8}{'stmts':>7}  verdict"
    )
    typer.echo("")
    typer.echo(header)
    typer.echo("-" * (len(header) + 22))
    for item in assessments:
        found = sum(1 for page in item.statement_pages.values() if page is not None)
        mark = "ok " if item.is_usable else "!! "
        broken = defects[item.doc_id]
        typer.echo(
            f"{item.doc_id:<18}{item.pages:>6}{item.chars_per_page:>10.0f}"
            f"{item.readable_ratio:>9.0%}{broken.rate:>8.1%}{broken.garbled_share:>8.0%}"
            f"{found:>7}  {mark}{item.verdict}"
        )

    problems = [item for item in assessments if not item.is_usable]
    for item in problems:
        typer.echo("")
        typer.echo(f"{item.doc_id}: {item.verdict}")
        for reason in item.reasons:
            typer.echo(f"  - {reason}")

    loud = [item for item in defects.values() if item.garbled_pages]
    if loud:
        typer.echo("")
        typer.echo("text layers that decoded to the wrong characters:")
        for broken in sorted(loud, key=lambda entry: -entry.garbled_share):
            listed = broken.garbled_pages[:8]
            pages = ", ".join(str(page.page) for page in listed)
            more = "" if len(broken.garbled_pages) <= 8 else f", +{len(broken.garbled_pages) - 8}"
            typer.echo(
                f"  {broken.doc_id}: {len(broken.garbled_pages)}/{len(broken.judged_pages)} "
                f"pages ({broken.garbled_share:.0%}), {broken.rate:.1%} of characters, "
                f"mode {'/'.join(broken.modes) or 'none'}"
            )
            typer.echo(f"    pages: {pages}{more}")

    # Reported separately from the garbled list, and not by rate: nothing is assigned in the
    # private use area, so every occurrence is unresolvable however few there are. 109 of
    # 2882-FY2024-AR's 110 such pages score under the garbled threshold, and its p26 holds 125
    # ticks encoded as U+F0FC in a board-expertise matrix -- row and column labels readable,
    # every tick gone. Whether it costs anything depends on what the glyph was, so the page
    # numbers are printed for someone to look at rather than judged here.
    unresolvable = [item for item in defects.values() if item.unreadable_pages]
    if unresolvable:
        typer.echo("")
        typer.echo("pages carrying characters no reader can resolve (private use area):")
        for broken in sorted(unresolvable, key=lambda entry: -len(entry.unreadable_pages)):
            worst = max(broken.unreadable_pages, key=lambda page: page.private_use)
            typer.echo(
                f"  {broken.doc_id}: {len(broken.unreadable_pages)} page(s), "
                f"{broken.unreadable_characters} character(s); worst p{worst.page} "
                f"({worst.private_use} on one page)"
            )

    target_path = paths.runs / "document_quality.json"
    target_path.write_text(
        json.dumps(
            {
                "documents": [item.to_json() for item in assessments],
                "text_layer_defects": {
                    doc_id: {
                        "off_core_rate": round(item.rate, 5),
                        "garbled_pages": len(item.garbled_pages),
                        "judged_pages": len(item.judged_pages),
                        "garbled_share": round(item.garbled_share, 4),
                        "modes": list(item.modes),
                        # Not rate-gated: see PageDefects.has_unreadable.
                        "unreadable_pages": [
                            {"page": page.page, "characters": page.private_use}
                            for page in sorted(
                                item.unreadable_pages,
                                key=lambda entry: -entry.private_use,
                            )[:20]
                        ],
                        "unreadable_page_count": len(item.unreadable_pages),
                        "unreadable_characters": item.unreadable_characters,
                        "worst_pages": [
                            {"page": page.page, "rate": round(page.rate, 4), "mode": page.mode}
                            for page in sorted(item.garbled_pages, key=lambda entry: -entry.rate)[
                                :10
                            ]
                        ],
                    }
                    for doc_id, item in sorted(defects.items())
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    # The verdicts are facts about documents, not judgements about the study.
    # `missing_financial_statements` is expected for a FY2024 annual report -- those
    # are paired with a 財務報告書 on purpose (DECISIONS D-012). Only a broken text
    # layer makes a document unusable as evidence (D-013).
    unreadable = [
        item
        for item in assessments
        if item.verdict in {"unusable_text_layer", "partially_unusable_text_layer"}
    ]
    narrative_only = [
        item for item in assessments if item.verdict == "missing_financial_statements"
    ]

    typer.echo("")
    typer.echo(f"readable text layer : {len(assessments) - len(unreadable)}/{len(assessments)}")
    typer.echo(f"narrative only      : {len(narrative_only)} (expected; paired with a 財務報告書)")
    typer.echo(f"unusable as evidence: {len(unreadable)}")
    typer.echo(f"wrote               : {target_path.relative_to(paths.root)}")

    if unreadable:
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
