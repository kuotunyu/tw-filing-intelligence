"""Build the BM25 half of the index from a parser's ``chunks.jsonl``.

    uv run python scripts/build_bm25.py
    uv run python scripts/build_bm25.py --parser candidate

**This script exists because the index it builds had no script.** `postings.json` and its
manifest were sitting in `data/index/*/` with nothing in the repository that produced them:
nothing called :func:`twfi.index.lexical.save_index`, so they had been built by an ad-hoc command
in a session that is gone. An artifact that cannot be re-derived cannot support a claim, and BM25
is the whole of F0's and F1's retrieval and half of F2's fusion -- so of the two index halves it
is the one least able to afford an unknown provenance.

Reads `chunks.jsonl` rather than re-parsing the PDFs, deliberately: the lexical and dense halves
must index *the same* chunks in *the same order*, because RRF fuses them by rank over a shared
document numbering. Re-chunking here would let the two halves drift apart silently -- the fusion
would still return results, for a corpus that exists in neither index.

Runs on CPU in seconds and needs no model. Nothing here is committed (CLAUDE.md rule 7).
"""

from __future__ import annotations

from typing import Annotated

import typer

from twfi.console import use_utf8_output
from twfi.index.embeddings import VECTOR_MANIFEST, utc_now
from twfi.index.lexical import Bm25Config, Bm25Index, Bm25Manifest, save_index
from twfi.io.jsonl import read_lines
from twfi.parsing.baseline import PARSER_NAME as BASELINE_PARSER
from twfi.parsing.layout import PARSER_NAME as LAYOUT_PARSER
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

PARSERS = ("baseline", "candidate")


@app.command()
def main(
    parser: Annotated[str, typer.Option(help="baseline, candidate, or both.")] = "both",
    k1: Annotated[float, typer.Option(help="Term-frequency saturation.")] = 1.2,
    b: Annotated[float, typer.Option(help="Length normalisation.")] = 0.75,
) -> None:
    """Index one or both parsers' chunks for BM25."""
    if parser not in {"baseline", "candidate", "both"}:
        typer.echo(f"unknown parser {parser!r}; choose baseline, candidate or both")
        raise typer.Exit(code=2)

    paths = repo_paths()
    wanted = PARSERS if parser == "both" else (parser,)
    built = 0
    for which in wanted:
        directory = paths.root / "data" / "index" / which
        source = directory / "chunks.jsonl"
        if not source.is_file():
            typer.echo(f"{which}: no chunks.jsonl; run build_index.py first")
            continue
        rows = read_lines(source)
        if not rows:
            typer.echo(f"{which}: chunks.jsonl is empty")
            continue
        texts = [str(row.get("text", "")) for row in rows]
        index = Bm25Index.build(texts, Bm25Config(k1=k1, b=b))
        manifest = Bm25Manifest(
            parser=BASELINE_PARSER if which == "baseline" else LAYOUT_PARSER,
            rows=len(index),
            config=index.config,
            built_at=utc_now(),
            documents=tuple(sorted({str(row.get("doc_id", "")) for row in rows})),
            chunk_ids=tuple(str(row.get("chunk_id", "")) for row in rows),
            notes="lexical half; dense vectors live beside this",
        )
        postings_path, manifest_path = save_index(directory, index, manifest)
        typer.echo(f"{which}: {len(index):,} documents, {len(index.postings):,} terms")
        typer.echo(f"  wrote {postings_path.relative_to(paths.root)}")
        typer.echo(f"  wrote {manifest_path.relative_to(paths.root)}")
        # Not fatal: the lexical half is usable on its own, and BM25 is often rebuilt first
        # after a re-chunk. But a mismatch here is the state RRF must never run in, so it is
        # said out loud rather than left for the fusion to discover.
        if not (directory / VECTOR_MANIFEST).is_file():
            typer.echo(f"  NOTE: no {VECTOR_MANIFEST} beside it; dense and hybrid are unavailable")
        built += 1

    if not built:
        raise typer.Exit(code=2)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
