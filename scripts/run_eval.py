"""Run the factor ladder end to end and score it.

    uv run python scripts/run_eval.py --set dev --factor F0 --factor F3
    uv run python scripts/run_eval.py --set dev            # every implemented rung

**All eight rungs now exist.** `results/feasibility/` is still deliberately not written here --
that belongs to the locked run.

Every rung shares one prompt, one answer contract and one scorer (protocol 2.1). Through F6 the
only thing that differs is what evidence can reach the answer, which is the point of a
factor-at-a-time design: any difference is attributable to the factor named.

    F0  baseline parser, lexical retrieval, top-5
    F1  layout parser, lexical retrieval, top-5
    F2  layout parser, hybrid retrieval (RRF), top-5 of 20
    F3  F2 plus the cross-encoder reranker
    F4  F3 plus the deterministic numeric route
    F5  F4 plus VLM chart captions in the index (a different index; see build_captions.py)
    F6  F5 plus reading values off the original crop pixels
    F7  F6 plus typed dispatch

**F7 is the one rung that can lose ground, and that is what makes it a test.** F0-F6 only ever
*add* a route and try each in a fixed order, so a new route can only help. F7 lets protocol 3.5's
mapping decide which route runs, so a misrouted question now costs an answer instead of falling
through to the next path. Reporting F7 below F6 is a finding about the router, not a regression
to be tuned away.

Writes `results/runs/ladder_<set>.json` with per-question rows -- answer, citations, latency,
tokens, every scored verdict -- so a later session can re-derive any summary figure or pair a
new run against this one.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import numpy as np
import typer

from twfi.answer.generate import GenerationConfig, generate
from twfi.answer.prompt import DEFAULT_VARIANT, PROMPT_VARIANTS, build_prompt, parse_answer
from twfi.console import use_utf8_output
from twfi.errors import EvaluationError, ProtocolLockError, ResultIntegrityError
from twfi.eval.answers import is_refusal, normalise_text, refusal_rates, score_answer
from twfi.eval.artifacts import build_error_analysis, build_summary, graded_record
from twfi.eval.citations import CitationGrader
from twfi.eval.dispatch import complete_answer
from twfi.eval.gates import wilson_interval
from twfi.eval.gold import GoldRecord, load_gold
from twfi.eval.locked_run import (
    begin_locked_run,
    locked_request_problems,
    resource_measurements,
)
from twfi.eval.protocol_lock import assert_lock_valid
from twfi.eval.results import PROBE_RUN, verify
from twfi.index.embeddings import load_vectors
from twfi.index.lexical import load_index
from twfi.index.rerank import load_cross_encoder, rerank_hits
from twfi.index.retrieve import Retriever, covered_targets, hit_rank, reciprocal_rank
from twfi.index.scope import company_document_scope
from twfi.io.acquire import expected_artifacts
from twfi.io.hashing import sha256_text_file
from twfi.io.jsonl import dump_lines, read_lines
from twfi.io.manifest import (
    load_acquisition_lock,
    load_document_manifest,
    load_structured_manifest,
    verify_acquisition,
)
from twfi.numeric.route import answer_numerically
from twfi.numeric.store import NumericStore
from twfi.paths import repo_paths
from twfi.protocol import (
    COVERAGE_AT,
    FACTOR_IDS,
    MRR_AT,
    RECALL_AT,
    TOP_K_RERANK,
    TOP_K_RETRIEVE,
)
from twfi.router.classify import classify, confusion_matrix, route_accuracy

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
    "F4": {
        "parser": "candidate",
        "mode": "hybrid",
        "rerank": True,
        "numeric": True,
        "what": "+ numeric route",
    },
    # F5 changes the *index*, not the answering path: chart captions become retrievable. It
    # therefore reads a separately built index (scripts/build_captions.py) so that adding
    # captions cannot silently invalidate the F0-F4 numbers, whose chunk digests are pinned.
    "F5": {
        "parser": "candidate_captioned",
        "mode": "hybrid",
        "rerank": True,
        "numeric": True,
        "what": "+ chart captions in index",
    },
    "F6": {
        "parser": "candidate_captioned",
        "mode": "hybrid",
        "rerank": True,
        "numeric": True,
        "chart": True,
        "what": "+ chart crop answering",
    },
    # F7 is the only rung that changes *which* route runs. Up to here every route that exists is
    # tried in a fixed order; here protocol 3.5's mapping decides, which is what a typed router
    # is for and also what makes it falsifiable -- a wrong route now costs an answer.
    "F7": {
        "parser": "candidate_captioned",
        "mode": "hybrid",
        "rerank": True,
        "numeric": True,
        "chart": True,
        "dispatch": True,
        "what": "+ typed dispatch",
    },
}

#: Named so the output can say what is missing instead of implying the ladder is complete.
NOT_IMPLEMENTED: dict[str, str] = {}


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


#: Protocol 2.5 caps a question at three crops. Reading more would let the chart route brute-force
#: a page, which is a different system from the one being measured.
MAX_CROPS = 3


def _figures_for(paths: Any, lock: Any, doc_id: str) -> tuple[Any, ...]:
    """Chart-shaped regions in one filing, detected once and cached by the caller.

    Ruled tables are *not* excluded here. Protocol 3.5 maps `table_cell` to the chart route --
    it is the "chart/table" rung and reads values out of rendered structures, tables included --
    so filtering tables out would remove the very regions most dev questions need.
    """
    from twfi.parsing.figures import chart_candidates, detect_figures

    acquired = lock.get(doc_id)
    if acquired is None or not acquired.local_path(paths.root).is_file():
        return ()
    return chart_candidates(detect_figures(acquired.local_path(paths.root)), [])


def _answer_from_chart(
    question: str,
    passages: list[Any],
    figures_by_doc: dict[str, tuple[Any, ...]],
    paths: Any,
    lock: Any,
    crop_dir: Path,
    config: Any,
) -> Any | None:
    """Try to read the answer off a crop on one of the retrieved pages.

    The crops are chosen by *retrieval*, not by the gold record: whichever pages the pipeline
    surfaced are the pages this may look at. Picking the page from gold would be answering the
    question with the answer key, the same trap `twfi.numeric.route` avoids.

    Returns ``None`` when no retrieved page carries a readable region, which is a refusal by
    absence rather than a guess.
    """
    from twfi.chart.crop_answer import answer_from_crop

    wanted: list[tuple[str, Any]] = []
    for hit in passages:
        figures = figures_by_doc.setdefault(hit.doc_id, _figures_for(paths, lock, hit.doc_id))
        wanted.extend((hit.doc_id, figure) for figure in figures if figure.page in set(hit.pages))
        if len(wanted) >= MAX_CROPS:
            break
    for doc_id, figure in wanted[:MAX_CROPS]:
        acquired = lock.get(doc_id)
        if acquired is None:
            continue
        answer = answer_from_crop(
            question,
            acquired.local_path(paths.root),
            doc_id,
            figure,
            crop_dir,
            config=config,
        )
        if answer.ok:
            return answer
    return None


def _retrievers(
    paths: Any,
    embed: Callable[[str], np.ndarray],
    depth: int,
    parsers: tuple[str, ...] = ("baseline", "candidate"),
) -> dict[str, Any]:
    """One retriever per parser, refusing any index that cannot answer for itself."""
    built: dict[str, Any] = {}
    for parser in parsers:
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


def _locked_data_problems(paths: Any, lock: Any) -> list[str]:
    """Gate G1 preflight, before the irreversible run marker is written."""
    documents = load_document_manifest(paths.documents_manifest)
    structured = load_structured_manifest(paths.structured_manifest)
    required_ids = {
        artifact.id for artifact in expected_artifacts(documents, structured) if artifact.required
    }
    required_ids.update(dataset.dataset_id for dataset in structured.automated())
    return verify_acquisition(lock, paths.root, expected_ids=required_ids)


def _gpu_preflight(config: GenerationConfig) -> list[str]:
    """Confirm local inference and reject a competing GPU workload."""
    query = [
        "nvidia-smi",
        "--query-compute-apps=process_name,used_memory",
        "--format=csv,noheader,nounits",
    ]
    observed = subprocess.run(query, capture_output=True, text=True, check=False)
    if observed.returncode != 0:
        return ["nvidia-smi is unavailable; the locked run cannot prove it is using the GPU"]
    problems: list[str] = []
    for line in observed.stdout.splitlines():
        name, separator, memory = line.partition(",")
        if not separator or "ollama" in name.casefold():
            continue
        if name.strip().casefold().endswith(("python.exe", "python")):
            problems.append(f"foreign GPU process is active: {name.strip()}")
            continue
        try:
            held = int(memory.strip())
        except ValueError:
            continue
        if held > 2_500:
            problems.append(f"foreign GPU process {name.strip()} holds {held} MiB")
    if problems:
        return problems
    readiness = generate("只回答 READY。", config)
    if not readiness.ok:
        return [f"generation model preflight failed: {readiness.error}"]
    after = subprocess.run(query, capture_output=True, text=True, check=False)
    if "ollama" not in after.stdout.casefold():
        return ["the generation preflight completed but no Ollama GPU process is visible"]
    return []


def _run_no_evidence_probes(
    paths: Any, config: GenerationConfig, prompt_variant: str
) -> list[dict[str, Any]]:
    """Run G8 with retrieval deliberately cleared, keeping it outside accuracy denominators."""
    probes = load_gold(paths.locked_probes.read_text(encoding="utf-8").splitlines())
    output: list[dict[str, Any]] = []
    for record in probes:
        decision = classify(record.question)
        prompt = build_prompt(record.question, [], variant=prompt_variant)
        completion = generate(prompt, config)
        draft = parse_answer(completion.text)
        refused = is_refusal(draft.answer)
        output.append(
            {
                "question_id": record.question_id,
                "factor": "F7",
                "category": "probe",
                "answerable": False,
                "gold_route": "unanswerable",
                "route": "unanswerable" if refused else decision.route,
                "handled_route": decision.route,
                "correct": refused,
                "refused": refused,
                "cited_ok": None,
                "question": record.question,
                "predicted": draft.answer,
                "expected_answer": record.answer,
                "route_decision": decision.to_json(),
                "retrieval": {"seconds": 0.0, "evidence_cleared": True},
                "generation": completion.to_json(),
            }
        )
    return output


def _write_locked_artifacts(
    *,
    paths: Any,
    rows: list[dict[str, Any]],
    gold: list[GoldRecord],
    probes: list[dict[str, Any]],
    lock_sha256: str,
    data_reproducible: bool,
) -> tuple[Path, tuple[Any, ...]]:
    """Write raw records first, then a summary derived exclusively from those records."""
    by_id = {record.question_id: record for record in gold}
    official: dict[str, list[dict[str, Any]]] = {factor: [] for factor in FACTOR_IDS}
    for row in rows:
        question_id = str(row.get("question_id", ""))
        if question_id not in by_id:
            raise ResultIntegrityError(f"no locked gold record for ladder row {question_id!r}")
        record = graded_record(row, by_id[question_id])
        official[record["factor"]].append(record)
    official[PROBE_RUN] = probes

    for run, records in official.items():
        dump_lines(paths.runs / run / "records.jsonl", records)

    budget_path = paths.runs / "resource_budget.json"
    try:
        budget = json.loads(budget_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultIntegrityError(f"cannot read {budget_path}: {exc}") from exc
    if not isinstance(budget, dict):
        raise ResultIntegrityError(f"{budget_path} must hold a JSON object")
    resources = resource_measurements(official["F7"], budget)
    resources_path = paths.runs / "resources.json"
    resources_path.write_text(
        json.dumps(resources, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = build_summary(
        official,
        protocol_lock_sha256=lock_sha256,
        resources=resources,
        data_reproducible=data_reproducible,
    )
    problems = verify(
        summary,
        official,
        expected_lock_sha256=lock_sha256,
        resources=resources,
    )
    if not problems:
        summary = build_summary(
            official,
            protocol_lock_sha256=lock_sha256,
            resources=resources,
            data_reproducible=data_reproducible,
            results_reproducible=True,
        )
        problems = verify(
            summary,
            official,
            expected_lock_sha256=lock_sha256,
            resources=resources,
        )

    paths.summary_json.parent.mkdir(parents=True, exist_ok=True)
    paths.summary_json.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    dump_lines(paths.error_analysis_jsonl, build_error_analysis(official["F7"]))
    verification = {
        "summary": str(paths.summary_json),
        "raw": str(paths.runs),
        "records_per_run": {run: len(records) for run, records in official.items()},
        "reproducible": not problems,
        "problems": [problem.to_json() for problem in problems],
    }
    (paths.feasibility / "results_verification.json").write_text(
        json.dumps(verification, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return paths.summary_json, problems


@app.command()
def main(
    gold_set: Annotated[
        str, typer.Option("--set", help="dev, or locked after the irreversible freeze.")
    ] = "dev",
    factor: Annotated[list[str] | None, typer.Option("--factor", help="Rungs to run.")] = None,
    depth: Annotated[int, typer.Option(help="Candidates each side fetches before fusion.")] = 100,
    rerank_device: Annotated[str, typer.Option(help="cuda or cpu, for F3.")] = "cuda",
    limit: Annotated[int, typer.Option(help="Questions to run (0 = all).")] = 0,
    prompt_variant: Annotated[
        str, typer.Option("--prompt", help="Instruction wording variant.")
    ] = DEFAULT_VARIANT,
    numeric_db: Annotated[
        str,
        typer.Option(
            help="Store for F4. numeric.duckdb is gold-keyed; numeric_broad.duckdb is the "
            "whole-corpus ingest, which is what F4 would face in production."
        ),
    ] = "numeric_broad.duckdb",
    confirmed_locked: Annotated[
        bool,
        typer.Option(
            "--i-understand-this-is-the-only-locked-run",
            help="Required for --set locked after every preflight passes.",
        ),
    ] = False,
) -> None:
    """Run each requested rung over the gold set and score every answer."""
    paths = repo_paths()
    if gold_set not in {"dev", "locked"}:
        typer.echo(f"unknown --set {gold_set!r}; expected 'dev' or 'locked'")
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

    is_locked = gold_set == "locked"
    source = paths.locked_gold if is_locked else paths.dev_gold
    records: list[GoldRecord] = list(load_gold(source.read_text(encoding="utf-8").splitlines()))
    if limit:
        records = records[:limit]
    typer.echo(f"{len(records)} question(s); rungs {', '.join(wanted)}")
    if NOT_IMPLEMENTED:
        typer.echo(f"not implemented, so not run: {', '.join(sorted(NOT_IMPLEMENTED))}")

    needed = tuple(dict.fromkeys(LADDER[name]["parser"] for name in wanted))
    missing_index = [
        parser
        for parser in needed
        if not (paths.root / "data" / "index" / parser / "chunks.jsonl").is_file()
    ]
    if missing_index:
        typer.echo(
            f"no index for {', '.join(missing_index)}; F5-F7 read the captioned index -- "
            "run scripts/build_captions.py first"
        )
        raise typer.Exit(code=2)

    database = paths.duckdb / numeric_db
    if any(LADDER[name].get("numeric") for name in wanted) and not database.is_file():
        typer.echo(f"{database} does not exist; run load_historical.py first")
        raise typer.Exit(code=2)

    lock = load_acquisition_lock(paths.acquisition_lock)
    config = GenerationConfig()
    locked_lock_sha256 = ""
    if is_locked:
        preflight = locked_request_problems(
            factors=wanted,
            limit=limit,
            prompt_variant=prompt_variant,
            numeric_db=numeric_db,
        )
        if not confirmed_locked:
            preflight.append(
                "--i-understand-this-is-the-only-locked-run is required after reviewing preflight"
            )
        try:
            assert_lock_valid(paths.root, paths.protocol_lock_json)
        except ProtocolLockError as exc:
            preflight.append(str(exc))
        locked_lock_sha256 = (
            sha256_text_file(paths.protocol_lock_json) if paths.protocol_lock_json.is_file() else ""
        )
        preflight.extend(_locked_data_problems(paths, lock))
        budget_path = paths.runs / "resource_budget.json"
        if not budget_path.is_file():
            preflight.append(f"missing G10 measurement: {budget_path}")
        marker = paths.runs / "locked_run_started.json"
        if marker.exists():
            preflight.append(
                f"the locked run already started; preserve and inspect the marker at {marker}"
            )
        if preflight:
            typer.echo(f"{len(preflight)} locked-run preflight problem(s); nothing started:")
            for problem in preflight:
                typer.echo(f"  - {problem}")
            raise typer.Exit(code=2)
        gpu_problems = _gpu_preflight(config)
        if gpu_problems:
            typer.echo("locked-run GPU/model preflight failed; nothing started:")
            for problem in gpu_problems:
                typer.echo(f"  - {problem}")
            raise typer.Exit(code=3)
        try:
            begin_locked_run(
                marker,
                {
                    "started_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
                    "protocol_lock_sha256": locked_lock_sha256,
                    "factors": wanted,
                    "questions": len(records),
                    "prompt_variant": prompt_variant,
                    "numeric_db": numeric_db,
                },
            )
        except EvaluationError as exc:
            typer.echo(str(exc))
            raise typer.Exit(code=2) from exc
        typer.echo(
            f"locked run started; wrote irreversible marker {marker.relative_to(paths.root)}"
        )

    embed = _cpu_embedder()
    retrievers = _retrievers(paths, embed, depth, needed)
    score_pairs = None
    if any(LADDER[name]["rerank"] for name in wanted):
        typer.echo(f"loading the cross-encoder on {rerank_device} …")
        score_pairs, _ = load_cross_encoder(device=rerank_device)

    store = None
    if any(LADDER[name].get("numeric") for name in wanted):
        store = NumericStore(database)
        typer.echo(f"numeric store: {numeric_db}, {store.count():,} figure(s)")

    citation_grader = CitationGrader(
        {
            acquired.id: acquired.local_path(paths.root)
            for acquired in lock.records
            if acquired.kind == "document"
        }
    )
    crop_dir = paths.root / "data" / "cache" / "crops"
    # Detected once per document and reused across rungs: detect_figures walks every page, and
    # doing it per question would dominate the run.
    figures_by_doc: dict[str, tuple[Any, ...]] = {}

    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []

    for name in wanted:
        rung = LADDER[name]
        retriever, _manifest = retrievers[rung["parser"]]
        typer.echo("")
        typer.echo(f"{name} ({rung['what']}) …")
        scores = []
        latencies: list[float] = []
        decisions = []
        kinds: list[str] = []
        refused: list[bool] = []
        started_rung = time.perf_counter()
        for position, record in enumerate(records, start=1):
            retrieval_started = time.perf_counter()
            document_scope = company_document_scope(record.question)
            shortlist = retriever.search(
                record.question,
                TOP_K_RETRIEVE,
                mode=rung["mode"],
                allowed_doc_ids=document_scope,
            )
            if rung["rerank"] and score_pairs is not None:
                shortlist = rerank_hits(
                    record.question, shortlist, score_pairs=score_pairs, top_k=TOP_K_RETRIEVE
                )
            retrieval_seconds = time.perf_counter() - retrieval_started
            passages = shortlist[:TOP_K_RERANK]

            decision = classify(record.question)
            decisions.append(decision)
            kinds.append(record.question_type)
            prompt = build_prompt(record.question, passages, variant=prompt_variant)

            # Through F6 every available route is tried in a fixed order, so a route can only
            # add answers. F7 hands the choice to the router: `numeric` runs only for a question
            # routed to numeric, `chart` only for one routed to chart. That is what makes the
            # router falsifiable -- until now a misroute cost nothing.
            dispatch = rung.get("dispatch", False)
            may_try_numeric = rung.get("numeric") and store is not None
            may_try_chart = rung.get("chart")
            if dispatch:
                may_try_numeric = may_try_numeric and decision.route == "numeric"
                may_try_chart = may_try_chart and decision.route == "chart"

            # F4 tries the deterministic route on *every* question rather than a hand-picked
            # set: its parser needs a company, a period and a known account, so a narrative
            # question refuses on its own. Choosing which types get the SQL path would be
            # choosing where it wins.
            numeric = (
                answer_numerically(record.question, store)
                if may_try_numeric and store is not None
                else None
            )
            chart = None
            if not (numeric is not None and numeric.ok) and may_try_chart:
                chart = _answer_from_chart(
                    record.question, passages, figures_by_doc, paths, lock, crop_dir, config
                )

            answer = complete_answer(
                prompt,
                config,
                dispatch=dispatch,
                decision_route=decision.route,
                numeric=numeric,
                chart=chart,
            )
            completion = answer.completion
            draft = answer.draft
            score = score_answer(
                draft.answer,
                record,
                predicted_unit=draft.unit,
                predicted_period=draft.period,
            )
            citation = citation_grader.grade(
                record=record,
                predicted=draft.answer,
                cited=draft.cited,
                passages=passages,
                refused=score.refused,
                numeric=numeric if numeric is not None and numeric.ok else None,
                chart=chart if chart is not None and chart.ok else None,
            )
            scores.append(score)
            refused.append(score.refused)
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
                    "cited_ok": citation.valid,
                    "citation": {"kind": citation.kind, "detail": citation.detail},
                    "retrieval_scope": sorted(document_scope)
                    if document_scope is not None
                    else None,
                    "answer_in_prompt": _answer_reached_the_prompt(record, prompt),
                    "numeric_route": numeric.to_json() if numeric is not None else None,
                    "chart_route": chart.to_json() if chart is not None else None,
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
                    "route": decision.to_json(),
                    "handled_route": answer.handled_route,
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
                # Which store F4 read. The gold-keyed and whole-corpus stores give different
                # numbers, so a rate recorded without this is not interpretable.
                "numeric_db": numeric_db if rung.get("numeric") else None,
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
                # Protocol 3.5. Reported two ways: the router alone, which is what the
                # metric is defined over, and with the pipeline post-hoc refusal label,
                # because `unanswerable` is a gold route no pre-retrieval router can reach.
                "route_accuracy": round(route_accuracy(decisions, kinds), 4),
                "route_accuracy_with_refusals": round(route_accuracy(decisions, kinds, refused), 4),
                "route_confusion": {
                    f"{gold}->{got}": n
                    for (gold, got), n in sorted(confusion_matrix(decisions, kinds).items())
                },
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
            "Complete F0-F7 factor ladder. Development runs remain diagnostic; only the guarded "
            "post-freeze locked run writes results/feasibility and supports a GO/NO-GO decision."
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
    typer.echo(
        f"wrote: {(paths.runs / f'ladder_{gold_set}{suffix}_rows.jsonl').relative_to(paths.root)}"
    )

    verification_problems: tuple[Any, ...] = ()
    if is_locked:
        typer.echo("")
        typer.echo("running the five registered no-evidence probes with retrieval cleared ...")
        probes = _run_no_evidence_probes(paths, config, prompt_variant)
        summary_path, verification_problems = _write_locked_artifacts(
            paths=paths,
            rows=rows,
            gold=records,
            probes=probes,
            lock_sha256=locked_lock_sha256,
            data_reproducible=True,
        )
        typer.echo(f"wrote official locked artifacts and {summary_path.relative_to(paths.root)}")
        if verification_problems:
            typer.echo(
                f"G9 failed: {len(verification_problems)} summary/artifact disagreement(s) "
                "were preserved in results_verification.json"
            )

    failures = [row for row in rows if row["generation"]["error"]]
    if failures or verification_problems:
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
