"""F5: caption every chart region with the VLM, and write the captioned chunk set.

    nvidia-smi                                            # confirm GPU availability first
    uv run python scripts/build_captions.py --dry-run     # counts, no model, no writes
    uv run python scripts/build_captions.py
    uv run python scripts/build_index.py --parser candidate_captioned --device cpu
    uv run python scripts/build_bm25.py --parser candidate_captioned

**A separate index, on purpose.** Adding captions to `data/index/candidate/` would change the
chunk set that F0-F4 were measured on, and those runs pin `chunk_text_sha256` precisely so that
cannot happen quietly. F5 is a rung that changes the *index*, so it gets its own directory and
the earlier numbers stay describable.

**Resumable, because this is the long pole.** Roughly 1,700 chart regions across the corpus at a
few seconds each; a crash at region 1,400 must not mean starting over. Every caption is appended
to `data/cache/captions.jsonl` as it is produced and re-read on the next run, keyed by
``doc_id`` and ``crop_ref``. Re-running after a partial pass captions only what is missing.

**Captions never carry values.** :mod:`twfi.chart.caption` states why and the prompt enforces it;
this script only decides *which* regions get one. Protocol 2.4 allows the caption into the index
and nowhere else, and :func:`twfi.chart.crop_answer.answer_from_crop` has no parameter that could
accept one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any

import typer

from twfi.answer.generate import GenerationConfig
from twfi.chart.caption import Caption, caption_figure
from twfi.console import use_utf8_output
from twfi.io.jsonl import dump_lines, read_lines
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.figures import chart_candidates, detect_figures
from twfi.paths import repo_paths
from twfi.protocol import USABLE_DOCUMENTS

if TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

app = typer.Typer(add_completion=False, help=__doc__)

SOURCE_PARSER = "candidate"
TARGET_PARSER = "candidate_captioned"


def _load_cache(path: Path) -> dict[tuple[str, str], Caption]:
    """Captions already produced, keyed by document and crop."""
    if not path.is_file():
        return {}
    found: dict[tuple[str, str], Caption] = {}
    for row in read_lines(path):
        caption = Caption(
            doc_id=str(row["doc_id"]),
            page=int(row["page"]),
            crop_ref=str(row["crop_ref"]),
            text=str(row["text"]),
            model=str(row["caption_model"]),
            error=str(row.get("error") or ""),
        )
        found[caption.doc_id, caption.crop_ref] = caption
    return found


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Count regions without loading the model.")
    ] = False,
    limit: Annotated[int, typer.Option(help="Cap regions per document (0 = no cap).")] = 0,
) -> None:
    """Caption chart regions and assemble the captioned chunk set."""
    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    cache_path = paths.root / "data" / "cache" / "captions.jsonl"
    crop_dir = paths.root / "data" / "cache" / "crops"
    cache = _load_cache(cache_path)
    config = GenerationConfig()

    typer.echo(f"{len(cache):,} caption(s) already cached")
    produced: list[Caption] = []
    regions: dict[str, tuple[Any, ...]] = {}

    for document in USABLE_DOCUMENTS:
        acquired = lock.get(document.doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            continue
        pdf = acquired.local_path(paths.root)
        # Ruled tables are kept: protocol 3.5 routes `table_cell` to the chart/table rung, so a
        # statement page is a legitimate region for this route to describe and later to read.
        figures = chart_candidates(detect_figures(pdf), [])
        if limit:
            figures = figures[:limit]
        regions[document.doc_id] = figures
        # A cached *failure* is retried. The first run of this script recorded 16 of them because
        # ollama was not up, and treating those as done would have baked a transient outage into
        # the index permanently. Only a caption that succeeded counts as work already finished.
        pending = [
            f
            for f in figures
            if not (cached := cache.get((document.doc_id, f.crop_ref))) or not cached.ok
        ]
        typer.echo(f"  {document.doc_id:<18} {len(figures):>4} region(s), {len(pending):>4} to do")
        if dry_run:
            continue

        for position, figure in enumerate(pending, start=1):
            caption = caption_figure(pdf, document.doc_id, figure, crop_dir, config=config)
            cache[document.doc_id, figure.crop_ref] = caption
            produced.append(caption)
            # Appended as it goes: a crash at region 1,400 must not lose the first 1,399.
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(caption.to_json(), ensure_ascii=False) + "\n")
            if position % 25 == 0 or position == len(pending):
                typer.echo(f"    {document.doc_id}: {position:,}/{len(pending):,}")

    if dry_run:
        total = sum(len(v) for v in regions.values())
        typer.echo("")
        typer.echo(f"{total:,} region(s) total; --dry-run wrote nothing")
        return

    failed = sum(1 for caption in cache.values() if not caption.ok)
    typer.echo("")
    typer.echo(f"{len(cache):,} caption(s); {failed:,} unusable (kept, not indexed)")

    # ------------------------------------------------------------- the captioned chunk set
    source = paths.root / "data" / "index" / SOURCE_PARSER / "chunks.jsonl"
    if not source.is_file():
        typer.echo(f"{source} does not exist; run build_index.py --parser candidate first")
        raise typer.Exit(code=2)
    rows = read_lines(source)

    added = 0
    for doc_id, figures in regions.items():
        company = next(d.company_code for d in USABLE_DOCUMENTS if d.doc_id == doc_id)
        for figure in figures:
            described = cache.get((doc_id, figure.crop_ref))
            if described is None or not described.ok:
                continue
            box = figure.bbox
            rows.append(
                {
                    "chunk_id": f"{doc_id}:caption:{figure.crop_ref}",
                    "doc_id": doc_id,
                    "company_code": company,
                    "pages": [figure.page],
                    "bboxes": [
                        {"page": figure.page, "bbox": [box.x0, box.y0, box.x1, box.y1]},
                    ],
                    "section_path": [],
                    "kinds": ["figure"],
                    "parser": TARGET_PARSER,
                    "text": described.index_text(),
                }
            )
            added += 1

    target = paths.root / "data" / "index" / TARGET_PARSER / "chunks.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    dump_lines(target, rows)
    typer.echo(f"wrote {target.relative_to(paths.root)}: {len(rows):,} chunks ({added:,} captions)")
    typer.echo("")
    typer.echo("next, and both are needed before F5-F7 can run:")
    typer.echo(f"  uv run python scripts/build_index.py --parser {TARGET_PARSER} --device cpu")
    typer.echo(f"  uv run python scripts/build_bm25.py --parser {TARGET_PARSER}")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
