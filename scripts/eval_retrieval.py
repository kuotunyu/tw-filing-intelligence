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

Two tables come out, and which one answers which question matters:

* **``r@k`` compares modes within one parser, never the parsers.** At one ``top_k`` the baseline
  retrieves more text -- median chunk 800 raw characters against the candidate's 317 -- so a
  baseline-versus-candidate gap in this table is partly a chunk-size difference wearing a
  retrieval result's clothes (D-030). Mode comparisons are sound because those share a chunk set.
* **``recall at a matched character budget`` is the cross-parser table.** Both parsers are charged
  the same number of whitespace-stripped characters in rank order, so neither is rewarded for
  packing more into a chunk.

**What the budget does not equalise, stated because it was measured.** Recall is scored per page,
and a chunk spanning two pages is credited for both. The baseline covers 1.57 pages per chunk
against the candidate's 1.25, so at equal characters it still delivers 1.3-1.7x the distinct
pages. A gap in the budget table can therefore still be page-spanning generosity rather than
better retrieval. Each row carries ``median_pages`` beside ``median_chunks`` so the size of that
residual is visible rather than asserted away. Removing it entirely would mean scoring recall per
chunk, which D-029 rejected for measuring where the chunker drew its boundaries instead of whether
retrieval found the evidence -- so the bias is reported, not eliminated.

**Every difference comes with a paired exact McNemar p-value**, because these tables invite
comparisons they cannot support: with 15 dev questions covering four distinct (document, pageset)
targets, nothing in them separates at the 5% level. Per-question outcomes are written into the
artifact so a later run can be paired against this one -- the absence of them is why the 8/15
recorded in D-029 can never be compared to anything.

Writes `results/runs/retrieval_<set>.json` with both tables, so the numbers in DECISIONS D-029
can be re-derived rather than trusted.
"""

from __future__ import annotations

import itertools
import json
import time
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import typer

from twfi.console import use_utf8_output
from twfi.eval.gates import mcnemar_exact, wilson_interval
from twfi.eval.gold import GoldRecord, load_gold
from twfi.index.embeddings import chunk_text_digest, load_vectors, utc_now
from twfi.index.lexical import load_index
from twfi.index.retrieve import (
    Hit,
    Retriever,
    characters_of,
    covered_targets,
    hit_rank,
    recall_at_budget,
    recall_at_k,
    reciprocal_rank,
)
from twfi.io.hashing import sha256_text_file
from twfi.io.jsonl import read_lines
from twfi.paths import repo_paths
from twfi.protocol import COVERAGE_AT, MRR_AT, RECALL_AT

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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
    budget: Annotated[
        list[int] | None,
        typer.Option("--budget", help="Character budgets to report. The cross-parser comparison."),
    ] = None,
) -> None:
    """Report recall at each cutoff and at each character budget, per parser and mode."""
    paths = repo_paths()
    cutoffs = sorted(set(k or [10, 20]))
    # 8,000 characters is roughly what the baseline's top ten delivers (median chunk 800), so
    # the middle budget is comparable to the middle cutoff and the table can be read across.
    budgets = sorted(set(budget or [4_000, 8_000, 16_000]))
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
    budget_rows: list[dict[str, Any]] = []
    protocol_rows: list[dict[str, Any]] = []
    provenance: dict[str, Any] = {
        "measured_at": utc_now(),
        "gold_sha256": sha256_text_file(source),
        "question_ids": [record.question_id for record in records],
        "indexes": {},
    }
    typer.echo("")
    header = f"{'parser':<10} {'mode':<8} " + " ".join(f"{'r@' + str(c):>12}" for c in cutoffs)
    typer.echo(header)

    for parser in PARSERS:
        directory = paths.root / "data" / "index" / parser
        if not (directory / "chunks.jsonl").is_file():
            typer.echo(f"{parser}: no index; run build_index.py first")
            continue
        chunks = read_lines(directory / "chunks.jsonl")
        bm25, lexical_manifest = load_index(directory, expect_documents=len(chunks))
        # Through load_vectors, not np.load. This line used to read the array directly, which
        # bypassed every check that makes a vector set answerable for itself -- so the claim in
        # D-031 that `load_vectors(expect_rows=...)` would stop a stale index from being searched
        # was false on the one path that measures recall. A stale index here does not raise; it
        # returns confident numbers for chunks that no longer exist.
        try:
            vectors, vector_manifest = load_vectors(directory, expect_rows=len(chunks))
        except (FileNotFoundError, ValueError) as exc:
            typer.echo(f"{parser}: {exc}")
            raise typer.Exit(code=2) from exc
        drift = _chunk_id_drift(chunks, lexical_manifest, vector_manifest)
        if drift:
            typer.echo(f"{parser}: {drift}")
            raise typer.Exit(code=2)
        wrong_model = _model_mismatch(paths, vector_manifest)
        if wrong_model:
            typer.echo(f"{parser}: {wrong_model}")
            raise typer.Exit(code=2)
        provenance["indexes"][parser] = {
            "chunks": len(chunks),
            "chunk_text_sha256": vector_manifest.get("chunk_text_sha256"),
            "vectors_built_at": vector_manifest.get("built_at"),
            "postings_built_at": lexical_manifest.get("built_at"),
            "model_revision": vector_manifest.get("model_revision"),
            "embed_device": (vector_manifest.get("config") or {}).get("device"),
            "embed_dtype": (vector_manifest.get("config") or {}).get("dtype"),
            "tokeniser": lexical_manifest.get("tokeniser"),
        }
        retriever = Retriever(
            chunks=chunks, bm25=bm25, vectors=vectors, embed_query=embed, fetch_depth=depth
        )
        for mode in MODES:
            cells: list[str] = []
            # Protocol 3.2's four metrics, computed from one deep ranking per question so that
            # every cutoff reads off the same candidate pool.
            ranked_by_question = {
                record.question_id: retriever.search(record.question, depth, mode=mode)  # type: ignore[arg-type]
                for record in records
            }
            registered = _protocol_metrics(records, ranked_by_question)
            registered.update({"parser": parser, "mode": mode})
            protocol_rows.append(registered)

            for cutoff in cutoffs:
                started = time.perf_counter()
                outcomes: dict[str, bool] = {}
                for record in records:
                    outcomes[record.question_id] = recall_at_k(
                        retriever.search(record.question, cutoff, mode=mode),  # type: ignore[arg-type]
                        doc_id=record.source_document[0],
                        pages=list(record.page_numbers),
                    )
                hits = sum(outcomes.values())
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
                        # Per question, so a paired test is possible and so a later session can
                        # compare against this run without re-running it. The absence of these
                        # is why the 8/15 recorded in D-029 can never be paired against anything.
                        "outcomes": {qid: int(value) for qid, value in sorted(outcomes.items())},
                        "seconds": round(elapsed, 2),
                    }
                )
                cells.append(f"{hits:>3}/{len(records)} {low:.0%}-{high:.0%}")
            typer.echo(f"{parser:<10} {mode:<8} " + " ".join(f"{cell:>12}" for cell in cells))

            # The same questions again, budgeted by characters instead of by chunk count. This
            # is the comparison the ``r@k`` table above is not (D-030): a fixed chunk count
            # rewards whichever parser packs more into a chunk, a fixed character budget does
            # not. Fetched once at the deepest budget's worth of chunks and truncated per
            # budget, so the candidate pool does not move with the budget -- the same defect
            # that tying fetch depth to top_k produced.
            for budget_chars in budgets:
                started = time.perf_counter()
                budget_outcomes: dict[str, bool] = {}
                chunks_used: list[int] = []
                pages_seen: list[int] = []
                for record in records:
                    ranked = retriever.search(record.question, depth, mode=mode)  # type: ignore[arg-type]
                    budget_outcomes[record.question_id] = recall_at_budget(
                        ranked,
                        doc_id=record.source_document[0],
                        pages=list(record.page_numbers),
                        budget=budget_chars,
                    )
                    chunks_used.append(_chunks_within(ranked, budget_chars))
                    pages_seen.append(_pages_within(ranked, budget_chars))
                hits = sum(budget_outcomes.values())
                elapsed = time.perf_counter() - started
                low, high = wilson_interval(hits, len(records))
                budget_rows.append(
                    {
                        "parser": parser,
                        "mode": mode,
                        "budget_chars": budget_chars,
                        "outcomes": {
                            qid: int(value) for qid, value in sorted(budget_outcomes.items())
                        },
                        "n": len(records),
                        "correct": hits,
                        "rate": round(hits / len(records), 4),
                        "ci95": [round(low, 4), round(high, 4)],
                        # How many chunks the budget bought, which is what makes the comparison
                        # legible: the same budget is a different number of chunks per parser.
                        "median_chunks": sorted(chunks_used)[len(chunks_used) // 2],
                        # And how many distinct pages, which is the residual bias the character
                        # budget does *not* remove. Recall is scored per page, and the coarser
                        # chunker covers more pages per character (1.57 pages a chunk against
                        # 1.25), so a gap at matched characters can still be page-spanning
                        # generosity rather than better retrieval. Reported so the reader can see
                        # the size of it instead of being told it was handled.
                        "median_pages": sorted(pages_seen)[len(pages_seen) // 2],
                        "seconds": round(elapsed, 2),
                    }
                )

    if protocol_rows:
        typer.echo("")
        typer.echo("protocol 3.2 -- the four pre-registered metrics, scored on required_evidence")
        typer.echo(
            f"{'parser':<10} {'mode':<8} {'Recall@' + str(RECALL_AT):>9}"
            f"  {'MRR@' + str(MRR_AT):>7}  {'complete@' + str(COVERAGE_AT):>11}  {'x-page':>7}"
        )
        for entry in protocol_rows:
            typer.echo(
                f"{entry['parser']:<10} {entry['mode']:<8}"
                f" {entry[f'recall_at_{RECALL_AT}']:>6}/{entry['n']}"
                f"  {entry[f'mrr_at_{MRR_AT}']:>7.3f}"
                f"  {entry[f'complete_at_{COVERAGE_AT}']:>8}/{entry['n']}"
                f"  {entry['cross_page_covered']:>4}/{entry['cross_page_n']}"
            )
        typer.echo(
            "  (x-page applies only to questions whose evidence spans >=2 pages; dev has one)"
        )

    if budget_rows:
        typer.echo("")
        typer.echo("recall at a matched character budget -- this is the cross-parser comparison")
        header = f"{'parser':<10} {'mode':<8} " + " ".join(
            f"{str(value // 1000) + 'k chars':>16}" for value in budgets
        )
        typer.echo(header)
        for parser in PARSERS:
            for mode in MODES:
                series = [
                    row for row in budget_rows if row["parser"] == parser and row["mode"] == mode
                ]
                if not series:
                    continue
                cells = [
                    f"{row['correct']:>3}/{row['n']}"
                    f" ({row['median_chunks']:>2}ch {row['median_pages']:>2}pg)"
                    for row in sorted(series, key=lambda item: int(item["budget_chars"]))
                ]
                typer.echo(f"{parser:<10} {mode:<8} " + " ".join(f"{cell:>20}" for cell in cells))

    # Every pairwise comparison with its exact paired p-value. Printed because the tables above
    # invite comparisons they cannot support, and a reader not handed the p-value will make them.
    middle_cutoff = cutoffs[len(cutoffs) // 2]
    middle_budget = budgets[len(budgets) // 2]
    for label, series_rows, key, at in (
        (f"r@{middle_cutoff}", rows, "k", middle_cutoff),
        (f"{middle_budget // 1000}k chars", budget_rows, "budget_chars", middle_budget),
    ):
        lines = _significance_report(series_rows, key=key, cutoff=at)
        if not lines:
            continue
        typer.echo("")
        typer.echo(f"paired exact McNemar at {label} -- same {len(records)} questions:")
        for line in lines:
            typer.echo(line)
        # Printing fifteen p-values and marking the smallest "significant" is the multiplicity
        # error these tests exist to avoid, so the threshold that accounts for them is printed
        # beside them. The pre-registered comparison is F2's hybrid-versus-lexical within one
        # parser; everything else in this table is exploratory and needs the corrected threshold.
        corrected = 0.05 / len(lines)
        survivors = [
            line for line in lines if (value := _p_of(line)) is not None and value < corrected
        ]
        typer.echo(
            f"  ({len(lines)} comparisons; a Bonferroni threshold is {corrected:.4f}, and "
            f"{len(survivors)} of them clear it. A nominal p below 0.05 among fifteen tests is "
            "not a finding.)"
        )

    monotone = _monotonicity_problems(rows) + _monotonicity_problems(
        budget_rows, key="budget_chars"
    )
    payload = {
        "gold_set": gold_set,
        "fetch_depth": depth,
        "cutoffs": cutoffs,
        "budgets_chars": budgets,
        "questions": len(records),
        # What produced these numbers. The artifact carried none of this and is gitignored, so a
        # reader had no way to tell which index or which gold set a figure came from, and no way
        # to tell two runs apart. G1 asks that results be reconstructible; a table of rates with
        # no provenance is not.
        "provenance": provenance,
        # How many *distinct* evidence targets the questions actually cover. Recorded because the
        # rates read as n independent trials and are not: dev's 15 questions cover four distinct
        # (document, pageset) targets, one chunk carries eight of them, so a two-question
        # difference can be one chunk changing rank (protocol "DEV 集合的聚集性").
        "distinct_targets": len(
            {(record.source_document[0], record.page_numbers) for record in records}
        ),
        "protocol_rows": protocol_rows,
        "rows": rows,
        "budget_rows": budget_rows,
        "monotonicity_problems": monotone,
        "significance": {
            f"r@{middle_cutoff}": _significance_report(rows, key="k", cutoff=middle_cutoff),
            f"{middle_budget}chars": _significance_report(
                budget_rows, key="budget_chars", cutoff=middle_budget
            ),
        },
        "note": (
            "Page-level recall. Every rate carries n and a Wilson 95% interval; with a set this "
            "small the intervals overlap and a difference of two or three questions settles "
            "nothing. Retrieval settings may be chosen on dev only (protocol 1.3)."
        ),
        "cross_parser_comparison": (
            "Use `budget_rows`, not `rows`. `rows` reports recall at a fixed chunk count, and at "
            "one top_k the baseline retrieves about eight times the text -- median chunk 800 "
            "characters against 99 -- so that table rewards the larger chunker (D-030). Mode "
            "comparisons within one parser are sound in either table, because those share a chunk "
            "set. `budget_rows` charges each parser the same number of whitespace-stripped "
            "characters and carries `median_chunks` so the same budget's different chunk counts "
            "are visible."
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


def _chunk_id_drift(
    chunks: list[dict[str, Any]],
    lexical_manifest: dict[str, Any],
    vector_manifest: dict[str, Any],
) -> str:
    """Whether either index was built from different chunks than the ones on disk.

    The row-count checks above are necessary and not sufficient: two chunkings of the same corpus
    can produce the same number of chunks, and then a stale index passes every count and returns
    results for text that no longer exists. Both manifests record the first few chunk ids for
    exactly this comparison, so it costs nothing to make.

    The row counts and the chunk ids are both necessary and both insufficient. Correcting the
    merged-chunk heading line rewrote 1,515 of 4,796 candidate chunks while changing neither the
    count nor a single id, so an index built before that fix passed every earlier check here and
    would have returned numbers computed from text that no longer existed. Only the content
    digest catches it, which is why an index that does not carry one is refused outright.

    Returns an empty string when everything agrees, so the caller can treat it as a message.
    """
    head = [str(chunk.get("chunk_id", "")) for chunk in chunks[:5]]
    digest = chunk_text_digest([str(chunk.get("text", "")) for chunk in chunks])
    for name, manifest in (("lexical", lexical_manifest), ("dense", vector_manifest)):
        recorded = [str(value) for value in manifest.get("chunk_ids_head") or []]
        if not recorded:
            # An index built before the manifests carried ids. Nothing to compare, and saying so
            # is better than reporting agreement that was never checked.
            return (
                f"the {name} manifest records no chunk ids, so it cannot be shown to belong to "
                "this chunking; rebuild it"
            )
        if recorded != head[: len(recorded)]:
            return (
                f"the {name} index was built from different chunks: it records {recorded[0]!r} "
                f"first, this corpus starts with {head[0]!r}; rebuild rather than search it"
            )
        claimed = manifest.get("chunk_text_sha256")
        if not claimed:
            return (
                f"the {name} manifest records no chunk_text_sha256, so it cannot be shown to "
                "have been built from this chunk *text* -- a chunker fix can change the text "
                "while leaving the count and the ids alone; rebuild it"
            )
        if str(claimed) != digest:
            return (
                f"the {name} index was built from different chunk text: it records "
                f"{str(claimed)[:12]}… and this corpus hashes to {digest[:12]}…; the counts and "
                "ids match, so this is a content change -- rebuild rather than search it"
            )
    return ""


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


def _monotonicity_problems(rows: list[dict[str, Any]], *, key: str = "k") -> list[str]:
    """Recall must not fall as the cutoff, or the budget, rises within one parser and mode.

    Applies to both tables for the same reason: more of the same ranked list cannot find less.
    If it does, the candidate pool is moving with the cutoff -- the defect that tying fetch depth
    to ``top_k`` produced -- so this check exists to catch its return in either metric.
    """
    problems: list[str] = []
    by_series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        by_series.setdefault((str(row["parser"]), str(row["mode"])), []).append(row)
    for (parser, mode), series in sorted(by_series.items()):
        ordered = sorted(series, key=lambda item: int(item[key]))
        for earlier, later in itertools.pairwise(ordered):
            if int(later["correct"]) < int(earlier["correct"]):
                problems.append(
                    f"{parser}/{mode}: {key}={earlier[key]} gave {earlier['correct']} but "
                    f"{key}={later[key]} gave {later['correct']}"
                )
    return problems


def _significance_report(rows: list[dict[str, Any]], *, key: str, cutoff: Any) -> list[str]:
    """Every pairwise comparison at one cutoff, with its exact paired p-value.

    Printed because the numbers invite comparisons that they cannot support, and a reader who is
    not handed the p-value will make them anyway. With n=15 over four distinct gold targets, the
    honest finding is expected to be that nothing separates.
    """
    series = [row for row in rows if row[key] == cutoff and row.get("outcomes")]
    lines: list[str] = []
    for left, right in itertools.combinations(series, 2):
        only_left, only_right, probability = mcnemar_exact(left["outcomes"], right["outcomes"])
        verdict = "" if probability < 0.05 else "  not significant"
        lines.append(
            f"  {left['parser']}/{left['mode']} {left['correct']}"
            f" vs {right['parser']}/{right['mode']} {right['correct']}:"
            f" {only_left}+/{only_right}- discordant, p={probability:.3f}{verdict}"
        )
    return lines


def _protocol_metrics(
    records: Sequence[GoldRecord], ranked: Mapping[str, Sequence[Hit]]
) -> dict[str, Any]:
    """Protocol 3.2's four retrieval metrics over one gold set.

    Scored against ``required_evidence``, which is what the protocol defines them over, rather
    than against ``page_numbers``. The two agree on this corpus; using the field the protocol
    names is what stops them diverging later without anyone noticing.

    ``cross_page_n`` is reported beside its numerator because the metric only applies to
    questions whose evidence spans two or more pages, and on dev there is exactly one of those.
    A rate of 1/1 is not evidence of anything, and printing the denominator is what says so.
    """
    recall_hits: dict[str, bool] = {}
    complete_hits: dict[str, bool] = {}
    reciprocal_total = 0.0
    cross_page_total = 0
    cross_page_hits = 0

    for record in records:
        targets = record.evidence_targets
        hits = ranked.get(record.question_id, ())
        rank = hit_rank(hits, targets)
        recall_hits[record.question_id] = rank is not None and rank <= RECALL_AT
        reciprocal_total += reciprocal_rank(hits, targets, k=MRR_AT)
        covered = covered_targets(hits, targets, k=COVERAGE_AT)
        complete = bool(targets) and covered == targets
        complete_hits[record.question_id] = complete
        if len({page for _, page in targets}) >= 2:
            cross_page_total += 1
            cross_page_hits += int(complete)

    total = len(records)
    recall_correct = sum(recall_hits.values())
    complete_correct = sum(complete_hits.values())
    recall_low, recall_high = wilson_interval(recall_correct, total)
    return {
        "n": total,
        f"recall_at_{RECALL_AT}": recall_correct,
        f"recall_at_{RECALL_AT}_rate": round(recall_correct / total, 4) if total else 0.0,
        f"recall_at_{RECALL_AT}_ci95": [round(recall_low, 4), round(recall_high, 4)],
        f"mrr_at_{MRR_AT}": round(reciprocal_total / total, 4) if total else 0.0,
        f"complete_at_{COVERAGE_AT}": complete_correct,
        "cross_page_covered": cross_page_hits,
        "cross_page_n": cross_page_total,
        "recall_outcomes": {qid: int(value) for qid, value in sorted(recall_hits.items())},
        "complete_outcomes": {qid: int(value) for qid, value in sorted(complete_hits.items())},
    }


def _p_of(line: str) -> float | None:
    """The p-value out of a formatted comparison line, or ``None`` if it has none."""
    marker = "p="
    start = line.find(marker)
    if start < 0:
        return None
    tail = line[start + len(marker) :].split()[0]
    try:
        return float(tail)
    except ValueError:
        return None


def _model_mismatch(paths: Any, vector_manifest: dict[str, Any]) -> str:
    """Whether the vectors were built by a model other than the pinned one.

    G1 asks that results be reconstructible from raw artifacts, and a recall number produced by
    an unrecorded or unpinned embedder is not: the same corpus under a different bge-m3 revision
    is a different index. The manifest records the revision the build resolved and
    `configs/models.lock.json` records the one the study pinned, so the comparison is available
    -- and it only became available today, because while both index halves wrote `manifest.json`
    the lexical build overwrote the revision before anyone could read it (D-034).

    Returns an empty string when they agree, so the caller can treat it as a message. A manifest
    recording no revision is refused rather than passed: "not checked" is not "checked and fine".
    """
    built = vector_manifest.get("model_revision")
    if not built:
        return (
            "the vector manifest records no model revision, so which embedder produced these "
            "vectors cannot be established; rebuild the index"
        )
    try:
        pinned = json.loads(paths.models_lock_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"cannot read {paths.models_lock_json.name} to check the model pin: {exc}"
    role = (pinned.get("roles") or {}).get("embedding") or {}
    expected = role.get("revision_observed")
    if not expected:
        return "configs/models.lock.json pins no embedding revision; run pin_models.py"
    if str(built) != str(expected):
        return (
            f"the vectors were built by bge-m3 revision {built} but the study pins "
            f"{expected}; the same corpus under a different revision is a different index"
        )
    return ""


def _pages_within(hits: Sequence[Hit], budget: int) -> int:
    """How many distinct ``(doc, page)`` pairs a budget delivers.

    The quantity that reveals what the character budget does not equalise. Recall is judged per
    page, and a chunk spanning two pages is credited for both, so a parser whose chunks span more
    pages wins page-level recall at equal characters without retrieving better.
    """
    spent = 0
    pages: set[tuple[str, int]] = set()
    for hit in hits:
        pages.update((hit.doc_id, page) for page in hit.pages)
        spent += characters_of(hit.text)
        if spent >= budget:
            break
    return len(pages)


def _chunks_within(hits: Sequence[Hit], budget: int) -> int:
    """How many chunks a budget buys, counting the one that crosses it.

    Reported beside each budgeted rate because the same budget is a different number of chunks
    per parser, and that difference is the thing the budget exists to neutralise -- so it should
    be visible rather than implied.
    """
    spent = 0
    used = 0
    for hit in hits:
        used += 1
        spent += characters_of(hit.text)
        if spent >= budget:
            break
    return used


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
