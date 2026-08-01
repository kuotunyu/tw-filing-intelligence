"""Run the factor ladder end to end and score it.

    uv run python scripts/run_eval.py --set dev --factor F0 --factor F3
    uv run python scripts/run_eval.py --set dev            # every implemented rung

**Only the rungs that exist.** Protocol 2 defines F0 through F7; F4 (numeric route), F5 and F6
(chart) and F7 (typed routing) need modules this repository does not have yet, so this script
runs F0 through F3 and *says* which rungs it skipped rather than reporting a partial ladder as a
whole one. A ladder missing rungs cannot support a GO decision, and `results/feasibility/` is
deliberately not written here -- that belongs to the locked run.

Every rung shares one prompt, one answer contract and one scorer (protocol 2.1). The only thing
that differs between them is which chunks reach the prompt, which is the entire point of a
factor-at-a-time design: any difference in the numbers is attributable to the factor named.

    F0  baseline parser, lexical retrieval, top-5
    F1  layout parser, lexical retrieval, top-5
    F2  layout parser, hybrid retrieval (RRF), top-5 of 20
    F3  F2 plus the cross-encoder reranker

Writes `results/runs/ladder_<set>.json` with per-question rows -- answer, citations, latency,
tokens, every scored verdict -- so a later session can re-derive any summary figure or pair a
new run against this one.
"""

from __future__ import annotations

import json
import re
import statistics
import time
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import typer

from twfi.answer.generate import Generation, GenerationConfig, generate
from twfi.answer.prompt import DEFAULT_VARIANT, PROMPT_VARIANTS, build_prompt, parse_answer
from twfi.console import use_utf8_output
from twfi.eval.answers import normalise_text, refusal_rates, score_answer
from twfi.eval.gates import wilson_interval
from twfi.eval.gold import GoldRecord, load_gold
from twfi.index.embeddings import load_vectors
from twfi.index.lexical import load_index
from twfi.index.rerank import load_cross_encoder, rerank_hits
from twfi.index.retrieve import Retriever, covered_targets, hit_rank, reciprocal_rank
from twfi.io.hashing import sha256_text_file
from twfi.io.jsonl import dump_lines, read_lines
from twfi.paths import repo_paths
from twfi.protocol import COVERAGE_AT, MRR_AT, RECALL_AT, TOP_K_RERANK, TOP_K_RETRIEVE

if TYPE_CHECKING:
    from collections.abc import Callable

app = typer.Typer(add_completion=False, help=__doc__)

#: The rungs this script can actually run, and what each one changes. Kept as data so the
#: skipped rungs can be named in the output rather than silently absent.
LADDER: dict[str, dict[str, Any]] = {
    "F0": {"parser": "baseline", "mode": "lexical", "rerank": False, "what": "baseline parser"},
    "F1": {"parser": "candidate", "mode": "lexical", "rerank": False, "what": "+ layout parsing"},
    "F2": {"parser": "candidate", "mode": "hybrid", "rerank": False, "what": "+ hybrid retrieval"},
    "F3": {"parser": "candidate", "mode": "hybrid", "rerank": True, "what": "+ reranking"},
}

#: Named so the output can say what is missing instead of implying the ladder is complete.
NOT_IMPLEMENTED: dict[str, str] = {
    "F4": "numeric route (SQL over the DuckDB store)",
    "F5": "chart caption indexing",
    "F6": "original-crop chart answering",
    "F7": "typed bounded routing",
}


def _cpu_embedder() -> Callable[[str], np.ndarray]:
    """bge-m3 on CPU, memoised. The card is for generation and reranking."""
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


def _retrievers(paths: Any, embed: Callable[[str], np.ndarray], depth: int) -> dict[str, Any]:
    """One retriever per parser, refusing any index that cannot answer for itself."""
    built: dict[str, Any] = {}
    for parser in ("baseline", "candidate"):
        directory = paths.root / "data" / "index" / parser
        chunks = read_lines(directory / "chunks.jsonl")
        bm25, _ = load_index(directory, expect_documents=len(chunks))
        vectors, manifest = load_vectors(directory, expect_rows=len(chunks))
        built[parser] = (
            Retriever(
                chunks=chunks, bm25=bm25, vectors=vectors, embed_query=embed, fetch_depth=depth
            ),
            manifest,
        )
    return built


#: Figures in a gold answer, long enough not to be a year or a note reference.
_GOLD_FIGURE = re.compile(r"-?\d[\d,]*\.?\d*")


def _answer_reached_the_prompt(record: GoldRecord, prompt: str) -> bool | None:
    """Whether every figure the gold answer states appears in the prompt the model saw.

    A diagnostic, not a protocol metric -- it must never be used as a gate. It exists because
    page-level Recall@5 is not a proxy for "the model had the evidence": a page splits into
    several chunks, and the one that reaches the prompt need not be the one holding the figure.
    Reading Recall@5 that way is how D-040 concluded the bottleneck was over-refusal when six of
    its eight wrong refusals were the model correctly declining a prompt that did not contain the
    answer (D-041).

    ``None`` for a question with no figure in its gold answer -- a narrative or refusal question,
    where string presence says nothing.
    """
    if not record.answer:
        return None
    wanted = [
        normalise_text(figure) for figure in _GOLD_FIGURE.findall(record.answer) if len(figure) >= 3
    ]
    if not wanted:
        return None
    seen = normalise_text(prompt)
    return all(figure in seen for figure in wanted)


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="dev only until the protocol is frozen.")
    ] = "dev",
    factor: Annotated[list[str] | None, typer.Option("--factor", help="Rungs to run.")] = None,
    depth: Annotated[int, typer.Option(help="Candidates each side fetches before fusion.")] = 100,
    rerank_device: Annotated[str, typer.Option(help="cuda or cpu, for F3.")] = "cuda",
    limit: Annotated[int, typer.Option(help="Questions to run (0 = all).")] = 0,
    prompt_variant: Annotated[
        str, typer.Option("--prompt", help="Instruction wording variant.")
    ] = DEFAULT_VARIANT,
) -> None:
    """Run each requested rung over the gold set and score every answer."""
    paths = repo_paths()
    if gold_set != "dev":
        typer.echo(
            "refusing: only dev may be run before the freeze (protocol 1.3, step 7). The locked "
            "run happens after scripts/freeze_protocol.py, and this script does not write "
            "results/feasibility/ in any case."
        )
        raise typer.Exit(code=2)
    if prompt_variant not in PROMPT_VARIANTS:
        typer.echo(f"unknown --prompt {prompt_variant!r}; have {sorted(PROMPT_VARIANTS)}")
        raise typer.Exit(code=2)
    wanted = [name.upper() for name in (factor or list(LADDER))]
    unknown = [name for name in wanted if name not in LADDER]
    if unknown:
        missing = ", ".join(
            f"{name} ({NOT_IMPLEMENTED[name]})" for name in unknown if name in NOT_IMPLEMENTED
        )
        typer.echo(f"cannot run {unknown}: {missing or 'no such rung'}")
        raise typer.Exit(code=2)

    source = paths.dev_gold
    records: list[GoldRecord] = list(load_gold(source.read_text(encoding="utf-8").splitlines()))
    if limit:
        records = records[:limit]
    typer.echo(f"{len(records)} question(s); rungs {', '.join(wanted)}")
    typer.echo(f"not implemented, so not run: {', '.join(sorted(NOT_IMPLEMENTED))}")

    embed = _cpu_embedder()
    retrievers = _retrievers(paths, embed, depth)
    score_pairs = None
    if any(LADDER[name]["rerank"] for name in wanted):
        typer.echo(f"loading the cross-encoder on {rerank_device} …")
        score_pairs, _ = load_cross_encoder(device=rerank_device)

    config = GenerationConfig()
    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for name in wanted:
        rung = LADDER[name]
        retriever, _manifest = retrievers[rung["parser"]]
        typer.echo("")
        typer.echo(f"{name} ({rung['what']}) …")
        scores = []
        latencies: list[float] = []
        started_rung = time.perf_counter()
        for position, record in enumerate(records, start=1):
            retrieval_started = time.perf_counter()
            shortlist = retriever.search(record.question, TOP_K_RETRIEVE, mode=rung["mode"])
            if rung["rerank"] and score_pairs is not None:
                shortlist = rerank_hits(
                    record.question, shortlist, score_pairs=score_pairs, top_k=TOP_K_RETRIEVE
                )
            retrieval_seconds = time.perf_counter() - retrieval_started
            passages = shortlist[:TOP_K_RERANK]

            prompt = build_prompt(record.question, passages, variant=prompt_variant)
            completion: Generation = generate(prompt, config)
            draft = parse_answer(completion.text)
            score = score_answer(
                draft.answer,
                record,
                predicted_unit=draft.unit,
                predicted_period=draft.period,
            )
            scores.append(score)
            latencies.append(completion.seconds)

            targets = record.evidence_targets
            rank = hit_rank(shortlist, targets)
            rows.append(
                {
                    "factor": name,
                    "question_id": record.question_id,
                    "question_type": record.question_type,
                    "predicted": draft.answer,
                    "gold": record.answer,
                    "predicted_unit": draft.unit,
                    "predicted_period": draft.period,
                    "cited": list(draft.cited),
                    "answer_in_prompt": _answer_reached_the_prompt(record, prompt),
                    "cited_pages": [
                        {"doc_id": hit.doc_id, "pages": list(hit.pages)}
                        for hit in draft.cited_hits(passages)
                    ],
                    "retrieval": {
                        f"recall_at_{RECALL_AT}": rank is not None and rank <= RECALL_AT,
                        f"mrr_at_{MRR_AT}": round(reciprocal_rank(shortlist, targets, k=MRR_AT), 4),
                        f"complete_at_{COVERAGE_AT}": bool(targets)
                        and covered_targets(shortlist, targets, k=COVERAGE_AT) == targets,
                        "seconds": round(retrieval_seconds, 3),
                    },
                    "generation": completion.to_json(),
                    "score": score.to_json(),
                }
            )
            mark = "ok " if score.correct else "xx "
            if not completion.ok:
                mark = "!! "
            typer.echo(
                f"  {mark}{record.question_id} {position:>2}/{len(records)}"
                f"  {completion.seconds:>5.1f}s  {draft.answer[:44]}"
            )

        correct = sum(1 for score in scores if score.correct)
        low, high = wilson_interval(correct, len(scores))
        summary.append(
            {
                "factor": name,
                "what": rung["what"],
                "parser": rung["parser"],
                "mode": rung["mode"],
                "reranked": rung["rerank"],
                "n": len(scores),
                "correct": correct,
                "rate": round(correct / len(scores), 4) if scores else 0.0,
                "ci95": [round(low, 4), round(high, 4)],
                "exact_match": sum(1 for s in scores if s.exact),
                "mean_token_f1": round(statistics.fmean(s.f1 for s in scores), 4) if scores else 0,
                "unit_correct": sum(1 for s in scores if s.unit is True),
                "unit_applicable": sum(1 for s in scores if s.unit is not None),
                "period_correct": sum(1 for s in scores if s.period),
                "refusal": refusal_rates(scores),
                "generation_median_seconds": round(statistics.median(latencies), 2),
                "generation_max_seconds": round(max(latencies), 2) if latencies else 0.0,
                "rung_seconds": round(time.perf_counter() - started_rung, 1),
            }
        )

    typer.echo("")
    typer.echo(f"{'factor':<6} {'what':<22} {'correct':>9} {'EM':>4} {'F1':>6} {'gen p50':>8}")
    for entry in summary:
        typer.echo(
            f"{entry['factor']:<6} {entry['what']:<22}"
            f" {entry['correct']:>4}/{entry['n']:<4}"
            f" {entry['exact_match']:>4} {entry['mean_token_f1']:>6.3f}"
            f" {entry['generation_median_seconds']:>7.1f}s"
        )

    payload = {
        "gold_set": gold_set,
        "factors_run": wanted,
        "factors_not_implemented": NOT_IMPLEMENTED,
        "fetch_depth": depth,
        "top_k_retrieve": TOP_K_RETRIEVE,
        "top_k_rerank": TOP_K_RERANK,
        "prompt_variant": prompt_variant,
        "generation_model": config.model,
        "decoding": config.options,
        "gold_sha256": sha256_text_file(source),
        "questions": len(records),
        "summary": summary,
        "note": (
            "Partial ladder: F4-F7 are not implemented, so this cannot support a GO decision and "
            "results/feasibility/ is not written. Dev only; the locked run happens after the "
            "freeze."
        ),
    }
    suffix = "" if prompt_variant == DEFAULT_VARIANT else f"_{prompt_variant}"
    destination = paths.runs / f"ladder_{gold_set}{suffix}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    dump_lines(paths.runs / f"ladder_{gold_set}{suffix}_rows.jsonl", rows)
    typer.echo("")
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")
    typer.echo(f"wrote: {(paths.runs / f'ladder_{gold_set}_rows.jsonl').relative_to(paths.root)}")

    failures = [row for row in rows if row["generation"]["error"]]
    if failures:
        typer.echo("")
        typer.echo(f"{len(failures)} generation call(s) failed:")
        for row in failures[:5]:
            typer.echo(f"  {row['factor']} {row['question_id']}: {row['generation']['error']}")
        raise typer.Exit(code=1)


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
