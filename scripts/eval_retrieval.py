"""Measure retrieval recall over a gold set, for both parsers and all three modes.

    uv run python scripts/eval_retrieval.py --set dev
    uv run python scripts/eval_retrieval.py --set dev --depth 100 --k 10 --k 20

CPU only. The corpus vectors were built once on the GPU and persisted; a query needs one
embedding, which bge-m3 does on CPU in about 55 ms. So this is runnable while another project
has the card, and it is the reason the retrieval half of the study can progress without one.

**Defaults to dev, and that default is load-bearing.** Protocol 1.3 permits thresholds and
retrieval settings to be chosen on the development split only, and this script exists to choose
them. `--set locked` is available for a post-freeze run and prints a warning saying it must not
inform a tuning decision -- the same guard `compare_table_strategies.py` carries, for the same
reason: an earlier measurement in this project pooled locked and dev before anyone noticed.

Recall is page-level: a retrieved chunk counts if it covers a page the gold record cites. Gold
cites pages, a chunk may span two, and demanding the exact chunk would measure where the
chunker drew its boundaries rather than whether retrieval found the evidence.

**⚠️ Do not compare the two parsers on these numbers (D-030).** At one ``top_k`` the baseline
retrieves roughly eight times the text -- its chunks have a median of 800 characters against the
candidate's 99 -- and covers 1.57 pages per chunk against 1.12. Page-level recall at a fixed
chunk count therefore rewards whichever parser packs more in, so a baseline-versus-candidate
gap here is a chunk-size difference wearing a retrieval result's clothes. Comparing *modes*
within one parser is sound, because those share a chunk set. A fair cross-parser comparison
needs a matched character budget, which is not implemented yet.

Writes `results/runs/retrieval_<set>.json`, so the numbers in DECISIONS D-029 can be
re-derived rather than trusted.
"""

from __future__ import annotations

import itertools
import json
import time
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import typer

from twfi.console import use_utf8_output
from twfi.eval.gates import wilson_interval
from twfi.eval.gold import GoldRecord, load_gold
from twfi.index.embeddings import load_vectors
from twfi.index.lexical import load_index
from twfi.index.retrieve import Retriever, recall_at_k
from twfi.io.jsonl import read_lines
from twfi.paths import repo_paths

if TYPE_CHECKING:
    from collections.abc import Callable

app = typer.Typer(add_completion=False, help=__doc__)

MODES = ("lexical", "dense", "hybrid")
PARSERS = ("baseline", "candidate")


def _cpu_embedder() -> Callable[[str], np.ndarray]:
    """One query at a time on CPU, memoised.

    Memoised because a gold set asks each question once per parser per mode per k, and the
    embedding does not depend on any of those -- recomputing it would multiply the only slow
    step in the measurement by twelve.
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    torch.set_num_threads(6)
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    model = AutoModel.from_pretrained("BAAI/bge-m3", dtype=torch.float32).eval()
    cache: dict[str, np.ndarray] = {}

    def embed(query: str) -> np.ndarray:
        if query not in cache:
            with torch.inference_mode():
                encoded = tokenizer([query], return_tensors="pt", truncation=True, max_length=1024)
                hidden = torch.nn.functional.normalize(
                    model(**encoded).last_hidden_state[:, 0], p=2, dim=1
                )
            cache[query] = hidden[0].numpy().astype(np.float32)
        return cache[query]

    return embed


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="dev (the only set tuning may rest on) or locked.")
    ] = "dev",
    depth: Annotated[int, typer.Option(help="Candidates each side fetches before fusion.")] = 100,
    k: Annotated[list[int] | None, typer.Option("--k", help="Cutoffs to report.")] = None,
) -> None:
    """Report recall at each cutoff, for every parser and mode."""
    paths = repo_paths()
    cutoffs = sorted(set(k or [10, 20]))
    if gold_set not in {"dev", "locked"}:
        typer.echo(f"unknown set {gold_set!r}; choose dev or locked")
        raise typer.Exit(code=2)
    if gold_set == "locked":
        typer.echo(
            "NOTE: protocol 1.3 permits retrieval settings to be chosen on dev only. These "
            "numbers are a held-out check and must not inform a tuning decision."
        )

    source = paths.dev_gold if gold_set == "dev" else paths.locked_gold
    if not source.is_file():
        typer.echo(f"{source.relative_to(paths.root)} does not exist yet")
        raise typer.Exit(code=2)
    records: list[GoldRecord] = [
        record
        for record in load_gold(source.read_text(encoding="utf-8").splitlines())
        # A record naming no page gives retrieval nothing to hit, so including it would only
        # lower every number by a constant and make none of them mean anything more.
        if record.page_numbers
    ]
    if not records:
        typer.echo("no gold record in this set cites a page")
        raise typer.Exit(code=2)
    typer.echo(f"{len(records)} question(s) with a page citation, depth {depth}")

    embed = _cpu_embedder()
    rows: list[dict[str, Any]] = []
    header = f"{'parser':<10} {'mode':<8} " + " ".join(f"{'r@' + str(c):>12}" for c in cutoffs)
    typer.echo("")
    typer.echo(header)

    for parser in PARSERS:
        directory = paths.root / "data" / "index" / parser
        if not (directory / "chunks.jsonl").is_file():
            typer.echo(f"{parser}: no index; run build_index.py first")
            continue
        chunks = read_lines(directory / "chunks.jsonl")
        bm25, _ = load_index(directory, expect_documents=len(chunks))
        # Through load_vectors, not np.load. This line used to read the array directly, which
        # bypassed every check that makes a vector set answerable for itself -- so the claim in
        # D-031 that `load_vectors(expect_rows=...)` would stop a stale index from being searched
        # was false on the one path that measures recall. A stale index here does not raise; it
        # returns confident numbers for chunks that no longer exist.
        try:
            vectors, _ = load_vectors(directory, expect_rows=len(chunks))
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(f"{parser}: {exc}")
            raise typer.Exit(code=2) from exc
        retriever = Retriever(
            chunks=chunks, bm25=bm25, vectors=vectors, embed_query=embed, fetch_depth=depth
        )
        for mode in MODES:
            cells: list[str] = []
            for cutoff in cutoffs:
                started = time.perf_counter()
                hits = 0
                for record in records:
                    found = recall_at_k(
                        retriever.search(record.question, cutoff, mode=mode),  # type: ignore[arg-type]
                        doc_id=record.source_document[0],
                        pages=list(record.page_numbers),
                    )
                    hits += int(found)
                elapsed = time.perf_counter() - started
                low, high = wilson_interval(hits, len(records))
                rows.append(
                    {
                        "parser": parser,
                        "mode": mode,
                        "k": cutoff,
                        "n": len(records),
                        "correct": hits,
                        "rate": round(hits / len(records), 4),
                        "ci95": [round(low, 4), round(high, 4)],
                        "seconds": round(elapsed, 2),
                    }
                )
                cells.append(f"{hits:>3}/{len(records)} {low:.0%}-{high:.0%}")
            typer.echo(f"{parser:<10} {mode:<8} " + " ".join(f"{cell:>12}" for cell in cells))

    monotone = _monotonicity_problems(rows)
    payload = {
        "gold_set": gold_set,
        "fetch_depth": depth,
        "cutoffs": cutoffs,
        "questions": len(records),
        "rows": rows,
        "monotonicity_problems": monotone,
        "note": (
            "Page-level recall. Every rate carries n and a Wilson 95% interval; with a set this "
            "small the intervals overlap and a difference of two or three questions settles "
            "nothing. Retrieval settings may be chosen on dev only (protocol 1.3)."
        ),
        "cross_parser_comparison": (
            "NOT VALID on these numbers (D-030). At one top_k the baseline retrieves about eight "
            "times the text -- median chunk 800 characters against 99 -- so page-level recall at "
            "a fixed chunk count rewards the larger chunker. Mode comparisons within one parser "
            "are sound; a cross-parser comparison needs a matched character budget."
        ),
        "chunk_profile": _chunk_profile(paths),
    }
    destination = paths.runs / f"retrieval_{gold_set}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    typer.echo("")
    if monotone:
        # Recall cannot fall as the cutoff rises for one retriever over one candidate pool. If
        # it does, the pool is moving with the cutoff -- which is exactly the defect that tying
        # fetch depth to top_k produced, so this check exists to catch its return.
        typer.echo(f"{len(monotone)} monotonicity problem(s) -- recall fell as k rose:")
        for problem in monotone:
            typer.echo(f"  {problem}")
    else:
        typer.echo("recall is monotone in k everywhere, as one candidate pool requires")
    typer.echo("")
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")
    if monotone:
        raise typer.Exit(code=1)


def _chunk_profile(paths: Any) -> dict[str, Any]:
    """Chunk size per parser, recorded beside the recall numbers so the caveat travels with them.

    Without this a reader sees two recall columns and assumes they are comparable. The profile
    is the evidence that they are not.
    """
    profile: dict[str, Any] = {}
    for parser in PARSERS:
        path = paths.root / "data" / "index" / parser / "chunks.jsonl"
        if not path.is_file():
            continue
        rows = read_lines(path)
        lengths = sorted(len(str(row.get("text", ""))) for row in rows)
        pages = [len(row.get("pages", ()) or ()) for row in rows]
        profile[parser] = {
            "chunks": len(rows),
            "median_chars": lengths[len(lengths) // 2] if lengths else 0,
            "total_chars": sum(lengths),
            "mean_pages_per_chunk": round(sum(pages) / len(pages), 2) if pages else 0.0,
        }
    return profile


def _monotonicity_problems(rows: list[dict[str, Any]]) -> list[str]:
    """Recall must not fall as the cutoff rises within one parser and mode."""
    problems: list[str] = []
    by_series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_series.setdefault((str(row["parser"]), str(row["mode"])), []).append(row)
    for (parser, mode), series in sorted(by_series.items()):
        ordered = sorted(series, key=lambda item: int(item["k"]))
        for earlier, later in itertools.pairwise(ordered):
            if int(later["correct"]) < int(earlier["correct"]):
                problems.append(
                    f"{parser}/{mode}: r@{earlier['k']}={earlier['correct']} but "
                    f"r@{later['k']}={later['correct']}"
                )
    return problems


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
