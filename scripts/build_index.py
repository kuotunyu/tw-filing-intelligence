"""Embed both parsers' chunks and persist the dense index.

    uv run python scripts/build_index.py --device cuda
    uv run python scripts/build_index.py --device cpu          # slower, needs no card
    uv run python scripts/build_index.py --device cuda --parser candidate --limit 500

One index per parser, never pooled: F0's fixed windows and F1-F7's layout-aware chunks are
different corpora (5,815 chunks against 13,116), and a shared index would let the baseline
retrieve from the candidate's chunking -- turning a parsing comparison into nothing.

**``--device`` is required and has no default**, because neither choice is safe to assume.
``cuda`` must be deliberate (CLAUDE.md rule 8: another project may hold the card, and this loads
a model onto it). ``cpu`` must be deliberate too: it is roughly an order of magnitude slower, so
choosing it by accident looks like a hang. On CPU the dtype defaults to float32 -- half precision
there is emulated and slower, not cheaper -- which also matches the query path, since
`eval_retrieval.py` embeds its queries on CPU in float32.

BM25 is not built here. It is CPU-only and belongs with the fusion code; the dense vectors are
the only part with a reason to want a card.

Writes `data/index/<parser>/{vectors.npy,vectors.manifest.json}` plus `chunks.jsonl` so that a
retrieved row can be turned back into text, a page and a section path -- G4 scores citations,
so a vector without its provenance is not usable evidence. The manifest filename is *not*
`manifest.json`: the lexical build writes its own manifest into the same directory, and while
both used that name, building BM25 afterwards silently overwrote the embedding provenance.

Nothing here is committed: vectors and chunk dumps are build artifacts (CLAUDE.md rule 7).
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from twfi.console import use_utf8_output
from twfi.errors import ParsingError
from twfi.index.embeddings import (
    EmbeddingConfig,
    EmbeddingManifest,
    embed_texts,
    save_vectors,
    utc_now,
)
from twfi.io.jsonl import dump_lines
from twfi.io.manifest import load_acquisition_lock
from twfi.parsing.baseline import PARSER_NAME as BASELINE_PARSER
from twfi.parsing.baseline import chunk_fixed, parse_baseline
from twfi.parsing.chunker import chunk_structure_aware
from twfi.parsing.layout import PARSER_NAME as LAYOUT_PARSER
from twfi.parsing.layout import parse_layout
from twfi.paths import repo_paths
from twfi.protocol import USABLE_DOCUMENTS

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main(
    device: Annotated[
        str, typer.Option("--device", help="cuda or cpu. Required: neither is safe to assume.")
    ] = "",
    parser: Annotated[str, typer.Option(help="baseline, candidate, or both.")] = "both",
    limit: Annotated[int, typer.Option(help="Cap chunks per parser (0 = no cap).")] = 0,
    batch_size: Annotated[int, typer.Option(help="Encoder batch size.")] = 16,
    dtype: Annotated[str, typer.Option(help="float16 or float32. Defaults by device.")] = "",
) -> None:
    """Build the dense index for one or both parsers."""
    if device not in {"cuda", "cpu"}:
        typer.echo(
            "pass --device cuda (loads a model onto the card; check nvidia-smi first) or "
            "--device cpu (no card needed, roughly an order of magnitude slower)"
        )
        raise typer.Exit(code=2)
    # float16 on CPU is emulated: slower than float32 rather than cheaper. And float32 is what
    # the query path uses, so a CPU-built corpus matches its queries exactly.
    resolved_dtype = dtype or ("float16" if device == "cuda" else "float32")
    if resolved_dtype not in {"float16", "float32"}:
        typer.echo(f"unknown dtype {resolved_dtype!r}; choose float16 or float32")
        raise typer.Exit(code=2)
    if parser not in {"baseline", "candidate", "both"}:
        typer.echo(f"unknown parser {parser!r}; choose baseline, candidate or both")
        raise typer.Exit(code=2)

    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    wanted = ("baseline", "candidate") if parser == "both" else (parser,)

    for which in wanted:
        rows = _collect(which, lock, paths, limit=limit)
        if not rows:
            typer.echo(f"{which}: nothing to embed")
            continue
        typer.echo(f"{which}: embedding {len(rows):,} chunks on {device} ({resolved_dtype}) …")
        config = EmbeddingConfig(
            batch_size=batch_size,
            device=device,
            dtype="float16" if resolved_dtype == "float16" else "float32",
        )

        def report(done: int, total: int, *, which: str = which) -> None:
            if done % (batch_size * 40) == 0 or done == total:
                typer.echo(f"  {which}: {done:,}/{total:,}")

        vectors, revision = embed_texts([row["text"] for row in rows], config, progress=report)
        manifest = EmbeddingManifest(
            parser=BASELINE_PARSER if which == "baseline" else LAYOUT_PARSER,
            rows=vectors.shape[0],
            dimension=vectors.shape[1],
            config=config,
            built_at=utc_now(),
            documents=tuple(sorted({str(row["doc_id"]) for row in rows})),
            model_revision=revision,
            chunk_ids=tuple(str(row["chunk_id"]) for row in rows),
            notes="dense only; BM25 is computed separately on CPU",
        )
        directory = paths.root / "data" / "index" / which
        vector_path, manifest_path = save_vectors(directory, vectors, manifest)
        # Written through twfi.io.jsonl, which escapes U+2028/U+2029/U+0085. json.dumps leaves
        # those unescaped inside strings and str.splitlines() breaks on them, so a chunk of
        # filing prose carrying one was written as a single line and read back as two
        # fragments -- the first an unterminated string.
        chunk_path = directory / "chunks.jsonl"
        dump_lines(chunk_path, rows)
        typer.echo(f"  wrote {vector_path.relative_to(paths.root)} {vectors.shape}")
        typer.echo(f"  wrote {manifest_path.relative_to(paths.root)}")
        typer.echo(f"  wrote {chunk_path.relative_to(paths.root)}")


def _collect(which: str, lock: Any, paths: Any, *, limit: int) -> list[dict[str, Any]]:
    """Chunk every usable filing with one parser, carrying page and section provenance."""
    rows: list[dict[str, Any]] = []
    for document in USABLE_DOCUMENTS:
        acquired = lock.get(document.doc_id)
        if acquired is None or not acquired.local_path(paths.root).is_file():
            continue
        pdf = acquired.local_path(paths.root)
        try:
            if which == "baseline":
                chunks = chunk_fixed(parse_baseline(pdf, document.doc_id))
            else:
                chunks = chunk_structure_aware(parse_layout(pdf, document.doc_id))
        except ParsingError as exc:
            typer.echo(f"  {document.doc_id}: {exc}")
            continue
        for chunk in chunks:
            # The chunker already assigns a chunk_id and carries page refs and the section
            # path. Synthesising a new id here would have produced a second naming scheme
            # for the same object, and G4 matches citations against the chunker's.
            rows.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "doc_id": chunk.doc_id,
                    "company_code": document.company_code,
                    "pages": list(chunk.pages),
                    "bboxes": [
                        {
                            "page": ref.page,
                            "bbox": [ref.bbox.x0, ref.bbox.y0, ref.bbox.x1, ref.bbox.y1],
                        }
                        for ref in chunk.refs
                    ],
                    "section_path": list(chunk.section_path),
                    "kinds": list(chunk.kinds),
                    "parser": chunk.parser,
                    "text": chunk.text,
                }
            )
            if limit and len(rows) >= limit:
                return rows
    return rows


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
