"""Assemble locked-run records, reproducible summaries, and error analysis."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from twfi.errors import ResultIntegrityError
from twfi.eval.gates import wilson_interval
from twfi.eval.gold import GoldRecord
from twfi.eval.results import NUMERIC_ROUTE_CATEGORIES, PROBE_RUN, RawRecord, read_record
from twfi.protocol import (
    BASELINE_FACTOR,
    CANDIDATE_FACTOR,
    FACTOR_IDS,
    ROUTE_BY_QUESTION_TYPE,
)

__all__ = [
    "build_error_analysis",
    "build_summary",
    "graded_record",
    "nearest_rank_p95",
]


def graded_record(row: Mapping[str, Any], gold: GoldRecord) -> dict[str, Any]:
    """Add the flat fields the result verifier requires to one ladder row."""
    if row.get("question_id") != gold.question_id:
        raise ResultIntegrityError(
            f"ladder row {row.get('question_id')!r} does not match gold {gold.question_id!r}"
        )
    score = row.get("score")
    decision = row.get("route")
    if not isinstance(score, Mapping) or not isinstance(decision, Mapping):
        raise ResultIntegrityError(f"{gold.question_id}: ladder row has no score/route object")
    correct = score.get("correct")
    refused = score.get("refused")
    chosen = decision.get("route")
    if not isinstance(correct, bool) or not isinstance(refused, bool):
        raise ResultIntegrityError(f"{gold.question_id}: score verdicts must be booleans")
    if not isinstance(chosen, str) or not chosen:
        raise ResultIntegrityError(f"{gold.question_id}: route decision is missing")
    if "cited_ok" not in row:
        raise ResultIntegrityError(f"{gold.question_id}: citation was not graded")
    handled = row.get("handled_route")
    if not isinstance(handled, str) or not handled:
        raise ResultIntegrityError(f"{gold.question_id}: handled route is missing")

    payload = dict(row)
    payload["route_decision"] = decision
    payload.update(
        {
            "question_id": gold.question_id,
            "factor": str(row.get("factor", "")),
            "category": gold.question_type,
            "answerable": gold.answerable,
            "gold_route": ROUTE_BY_QUESTION_TYPE[gold.question_type],
            "route": "unanswerable" if refused else chosen,
            "handled_route": handled,
            "correct": correct,
            "refused": refused,
            "cited_ok": row["cited_ok"],
        }
    )
    parsed = read_record(payload, where=f"ladder/{gold.question_id}")
    if isinstance(parsed, str):
        raise ResultIntegrityError(parsed)
    return payload


def nearest_rank_p95(values: Sequence[float]) -> float:
    """Conservative empirical p95: the observation at rank ``ceil(.95*n)``."""
    if not values:
        raise ValueError("p95 needs at least one observation")
    ordered = sorted(float(value) for value in values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def _records(payloads: Sequence[Mapping[str, Any]], *, run: str) -> tuple[RawRecord, ...]:
    parsed: list[RawRecord] = []
    for index, payload in enumerate(payloads):
        record = read_record(payload, where=f"runs.{run}[{index}]")
        if isinstance(record, str):
            raise ResultIntegrityError(record)
        parsed.append(record)
    return tuple(parsed)


def _rate(numerator: int, denominator: int, *, label: str) -> dict[str, Any]:
    if denominator <= 0:
        raise ResultIntegrityError(f"cannot assemble {label}: no graded records")
    low, high = wilson_interval(numerator, denominator)
    return {
        "n": denominator,
        label: numerator,
        "rate": round(numerator / denominator, 6),
        "ci95": [round(low, 6), round(high, 6)],
    }


def build_summary(
    runs: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    protocol_lock_sha256: str,
    resources: Mapping[str, float],
    data_reproducible: bool,
    results_reproducible: bool = False,
) -> dict[str, Any]:
    """Compute every reported count from the official per-question records."""
    parsed = {run: _records(runs.get(run, ()), run=run) for run in (*FACTOR_IDS, PROBE_RUN)}
    factors: dict[str, Any] = {}
    for factor in FACTOR_IDS:
        records = parsed[factor]
        categories = sorted({record.category for record in records})
        factors[factor] = {
            "overall_accuracy": _rate(
                sum(record.correct for record in records), len(records), label="correct"
            ),
            "by_category": {
                category: _rate(
                    sum(record.correct for record in records if record.category == category),
                    sum(record.category == category for record in records),
                    label="correct",
                )
                for category in categories
            },
        }

    candidate = parsed[CANDIDATE_FACTOR]
    cited = [record for record in candidate if record.cited_ok is not None]
    numeric = [
        record
        for record in candidate
        if record.category in NUMERIC_ROUTE_CATEGORIES
        and record.answerable
        and record.handled_route == "numeric"
    ]
    unanswerable = [record for record in candidate if record.category == "unanswerable"]
    refusals = [record for record in candidate if record.refused]
    probes = parsed[PROBE_RUN]
    return {
        "protocol_lock_sha256": protocol_lock_sha256,
        "baseline": BASELINE_FACTOR,
        "candidate": CANDIDATE_FACTOR,
        "factors": factors,
        "citation_validity": _rate(
            sum(record.cited_ok is True for record in cited), len(cited), label="valid"
        ),
        "numeric_route_accuracy": _rate(
            sum(record.correct for record in numeric), len(numeric), label="correct"
        ),
        "route_accuracy": _rate(
            sum(record.route == record.gold_route for record in candidate),
            len(candidate),
            label="correct",
        ),
        "unanswerable": {
            **_rate(
                sum(not record.refused for record in unanswerable),
                len(unanswerable),
                label="over_answered",
            ),
            "refusal_precision": _rate(
                sum(not record.answerable for record in refusals),
                len(refusals),
                label="refused",
            ),
        },
        "probes": _rate(sum(record.refused for record in probes), len(probes), label="refused"),
        "resources": dict(resources),
        "checks": {
            "data_reproducible": data_reproducible,
            "results_reproducible": results_reproducible,
        },
    }


def build_error_analysis(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Bucket candidate failures without discarding their original diagnostic fields."""
    analysis: list[dict[str, Any]] = []
    for payload in records:
        record = read_record(payload, where=str(payload.get("question_id", "record")))
        if isinstance(record, str):
            raise ResultIntegrityError(record)
        buckets: list[str] = []
        generation = payload.get("generation")
        retrieval = payload.get("retrieval")
        if isinstance(generation, Mapping) and generation.get("error"):
            buckets.append("generation_error")
        if record.refused and record.answerable:
            buckets.append("incorrect_refusal")
        if not record.refused and not record.answerable:
            buckets.append("over_answer")
        if record.route != record.gold_route:
            buckets.append("route_error")
        if record.cited_ok is False:
            buckets.append("citation_invalid")
        if (
            not record.correct
            and isinstance(retrieval, Mapping)
            and retrieval.get("recall_at_5") is False
        ):
            buckets.append("retrieval_miss")
        numeric = payload.get("numeric_route")
        if isinstance(numeric, Mapping) and "template" in str(numeric.get("error", "")).lower():
            buckets.append("template_miss")
        if not record.correct and not buckets:
            buckets.append("answer_error")
        if buckets:
            analysis.append(
                {
                    "question_id": record.question_id,
                    "factor": record.factor,
                    "category": record.category,
                    "buckets": buckets,
                    "predicted": payload.get("predicted"),
                    "gold": payload.get("gold"),
                    "citation": payload.get("citation"),
                    "route": record.route,
                    "handled_route": record.handled_route,
                    "gold_route": record.gold_route,
                }
            )
    return analysis
