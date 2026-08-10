"""Deterministic post-hoc audit of the locked analysis, without changing the run.

The original run used :attr:`twfi.eval.answers.AnswerScore.correct`, whose text branch
requires exact match. Protocol 1.0.0 instead registered ``exact match OR token-F1 >= 0.8``.
This module keeps both readings visible: it re-scores every committed prediction against
the frozen gold with the runtime scorer, then applies the protocol-literal primary rule.
It never edits the frozen protocol, gold, run records, summary, gates, or verdict.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from twfi.errors import ResultIntegrityError
from twfi.eval.answers import AnswerScore, score_answer
from twfi.eval.gates import decide, evaluate, mcnemar_exact, wilson_interval
from twfi.eval.gold import GoldRecord, composition, load_gold
from twfi.io.hashing import sha256_text_file
from twfi.paths import RepoPaths
from twfi.protocol import BASELINE_FACTOR, CANDIDATE_FACTOR, FACTOR_IDS, HARD_CATEGORIES

__all__ = [
    "PROTOCOL_TEXT_F1_THRESHOLD",
    "build_analysis_audit",
    "protocol_literal_correct",
    "verify_committed_analysis_audit",
]

PROTOCOL_TEXT_F1_THRESHOLD: Final = 0.8


def protocol_literal_correct(
    score: AnswerScore, *, text_f1_threshold: float = PROTOCOL_TEXT_F1_THRESHOLD
) -> bool:
    """Apply Protocol §4's primary rule without changing the runtime scorer.

    Numeric and refusal decisions retain their registered semantics. Only non-numeric,
    answerable text receives the disjunction omitted by the runtime implementation.
    """
    if score.should_refuse:
        return score.refused
    if score.refused:
        return False
    if score.numeric is not None:
        return score.numeric
    return score.exact or score.f1 >= text_f1_threshold


def _json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResultIntegrityError(f"cannot read {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultIntegrityError(f"{path} must contain a JSON object")
    return payload


def _jsonl_objects(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResultIntegrityError(f"cannot read {path}: {exc}") from exc
    records: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResultIntegrityError(f"{path}:{number} is invalid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ResultIntegrityError(f"{path}:{number} must contain a JSON object")
        records.append(payload)
    return records


def _gold_by_id(path: Path) -> dict[str, GoldRecord]:
    try:
        records = load_gold(path.read_text(encoding="utf-8").splitlines())
    except (OSError, ValueError) as exc:
        raise ResultIntegrityError(f"cannot load frozen gold {path}: {exc}") from exc
    by_id = {record.question_id: record for record in records}
    if len(by_id) != len(records):
        raise ResultIntegrityError("frozen gold contains duplicate question ids")
    return by_id


def _optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResultIntegrityError(f"{field} must be text or null, got {type(value).__name__}")
    return value


def _regrade(payload: Mapping[str, Any], gold: GoldRecord) -> AnswerScore:
    predicted = payload.get("predicted")
    if not isinstance(predicted, str):
        raise ResultIntegrityError(f"{gold.question_id}: predicted answer must be text")
    return score_answer(
        predicted,
        gold,
        predicted_unit=_optional_text(payload, "predicted_unit"),
        predicted_period=_optional_text(payload, "predicted_period"),
    )


def _rate(successes: int, trials: int) -> dict[str, Any]:
    low, high = wilson_interval(successes, trials)
    return {
        "n": trials,
        "correct": successes,
        "rate": round(successes / trials, 6),
        "ci95": [round(low, 6), round(high, 6)],
    }


def _passed_rate(successes: int, trials: int) -> dict[str, Any]:
    if trials <= 0:
        raise ResultIntegrityError("a secondary proportion needs at least one record")
    return {"n": trials, "passed": successes, "rate": round(successes / trials, 6)}


def _retrieval(payload: Mapping[str, Any], *, question_id: str) -> Mapping[str, Any]:
    retrieval = payload.get("retrieval")
    if not isinstance(retrieval, Mapping):
        raise ResultIntegrityError(f"{question_id}: retrieval must be an object")
    return retrieval


def _candidate_secondary_metrics(
    payloads: Sequence[Mapping[str, Any]],
    gold_by_id: Mapping[str, GoldRecord],
    scores_by_id: Mapping[str, AnswerScore],
) -> dict[str, Any]:
    """Aggregate preregistered secondary metrics that survive in committed records."""
    retrieval_rows: list[tuple[bool, bool, float]] = []
    multi_target_complete: list[bool] = []
    route_labels: set[str] = set()
    route_pairs: list[tuple[str, str]] = []
    cited: list[bool] = []
    for payload in payloads:
        question_id = payload.get("question_id")
        if not isinstance(question_id, str) or question_id not in gold_by_id:
            raise ResultIntegrityError(f"candidate record has unknown question id {question_id!r}")
        retrieval = _retrieval(payload, question_id=question_id)
        recall = retrieval.get("recall_at_5")
        complete = retrieval.get("complete_at_5")
        mrr = retrieval.get("mrr_at_10")
        if not isinstance(recall, bool) or not isinstance(complete, bool):
            raise ResultIntegrityError(f"{question_id}: retrieval verdicts must be booleans")
        if not isinstance(mrr, int | float) or isinstance(mrr, bool):
            raise ResultIntegrityError(f"{question_id}: mrr_at_10 must be numeric")
        retrieval_rows.append((recall, complete, float(mrr)))
        if len(gold_by_id[question_id].evidence_targets) >= 2:
            multi_target_complete.append(complete)

        predicted_route = payload.get("route")
        if not isinstance(predicted_route, str):
            raise ResultIntegrityError(f"{question_id}: route must be text")
        gold_route = gold_by_id[question_id].route
        route_labels.update((gold_route, predicted_route))
        route_pairs.append((gold_route, predicted_route))

        cited_ok = payload.get("cited_ok")
        if cited_ok is not None:
            if not isinstance(cited_ok, bool):
                raise ResultIntegrityError(f"{question_id}: cited_ok must be boolean or null")
            cited.append(cited_ok)

    answerable_ids = [question_id for question_id, gold in gold_by_id.items() if gold.answerable]
    answerable_scores = [scores_by_id[question_id] for question_id in answerable_ids]
    numeric_scores = [score for score in answerable_scores if score.numeric is not None]
    unit_scores = [score for score in answerable_scores if score.unit is not None]
    unanswerable_scores = [
        scores_by_id[question_id] for question_id, gold in gold_by_id.items() if not gold.answerable
    ]
    refused_scores = [score for score in scores_by_id.values() if score.refused]
    labels = sorted(route_labels)
    return {
        "retrieval": {
            "recall_at_5": _passed_rate(
                sum(recall for recall, _, _ in retrieval_rows), len(retrieval_rows)
            ),
            "mrr_at_10": {
                "n": len(retrieval_rows),
                "mean": round(sum(mrr for _, _, mrr in retrieval_rows) / len(retrieval_rows), 6),
            },
            "complete_evidence_at_5": _passed_rate(
                sum(complete for _, complete, _ in retrieval_rows), len(retrieval_rows)
            ),
            "multi_target_complete_at_5": _passed_rate(
                sum(multi_target_complete), len(multi_target_complete)
            ),
        },
        "answer": {
            "exact_match": _passed_rate(
                sum(score.exact for score in answerable_scores), len(answerable_scores)
            ),
            "token_f1_mean": {
                "n": len(answerable_scores),
                "mean": round(
                    sum(score.f1 for score in answerable_scores) / len(answerable_scores), 6
                ),
            },
            "numeric_ok": _passed_rate(
                sum(score.numeric is True for score in numeric_scores), len(numeric_scores)
            ),
            "unit_ok": _passed_rate(
                sum(score.unit is True for score in unit_scores), len(unit_scores)
            ),
            "period_ok": _passed_rate(
                sum(score.period for score in answerable_scores), len(answerable_scores)
            ),
            "refusal_precision": _passed_rate(
                sum(score.should_refuse for score in refused_scores), len(refused_scores)
            ),
            "refusal_recall": _passed_rate(
                sum(score.refused for score in unanswerable_scores), len(unanswerable_scores)
            ),
        },
        "citation": {
            "validity": _passed_rate(sum(cited), len(cited)),
            "interpretation": "reaggregation of committed runtime cited_ok verdicts",
        },
        "routing": {
            "labels": labels,
            "confusion_matrix": {
                gold_route: {
                    predicted_route: sum(
                        actual_gold == gold_route and actual_predicted == predicted_route
                        for actual_gold, actual_predicted in route_pairs
                    )
                    for predicted_route in labels
                }
                for gold_route in labels
            },
        },
    }


def _metric_coverage() -> list[dict[str, str]]:
    """State what can be recovered, and refuse to promote collapsed citation verdicts."""
    rows = (
        ("recall_at_5", "recomputed", "candidate retrieval fields"),
        ("mrr_at_10", "recomputed", "candidate retrieval fields"),
        ("complete_evidence_coverage", "recomputed", "candidate retrieval fields"),
        ("multi_target_evidence_coverage", "recomputed", "frozen gold plus retrieval fields"),
        ("exact_match", "recomputed", "prediction plus frozen gold"),
        ("token_f1", "recomputed", "prediction plus frozen gold"),
        ("numeric_accuracy", "recomputed", "prediction plus frozen gold"),
        ("unit_accuracy", "recomputed", "prediction plus frozen gold"),
        ("period_accuracy", "recomputed", "prediction plus frozen gold"),
        ("refusal_precision_recall", "recomputed", "prediction plus frozen gold"),
        (
            "citation_validity",
            "reaggregated_runtime_verdict",
            "cited_ok is committed, but supporting passage text is not",
        ),
        (
            "citation_precision",
            "not_collected",
            "no citation-level relevance labels in committed artifacts",
        ),
        (
            "citation_recall",
            "not_collected",
            "no citation-level required-evidence match records",
        ),
        (
            "citation_page_correctness",
            "not_collected",
            "no independently graded citation-page records",
        ),
        (
            "citation_bbox_or_row_validity",
            "not_collected",
            "no candidate bbox/row verdicts in committed artifacts",
        ),
        ("route_confusion_matrix", "recomputed", "candidate route plus frozen gold route"),
        ("mcnemar_exact", "recomputed", "paired per-question answer outcomes"),
    )
    return [
        {"metric": metric, "status": status, "evidence": evidence}
        for metric, status, evidence in rows
    ]


def _literal_summary(
    summary: Mapping[str, Any],
    literal_by_factor: Mapping[str, Mapping[str, bool]],
    category_by_id: Mapping[str, str],
) -> dict[str, Any]:
    revised = copy.deepcopy(dict(summary))
    factors = revised.get("factors")
    if not isinstance(factors, dict):
        raise ResultIntegrityError("summary.factors must be an object")
    for factor in FACTOR_IDS:
        row = factors.get(factor)
        if not isinstance(row, dict):
            raise ResultIntegrityError(f"summary.factors.{factor} must be an object")
        outcomes = literal_by_factor[factor]
        row["overall_accuracy"] = _rate(sum(outcomes.values()), len(outcomes))
        by_category = row.get("by_category")
        if not isinstance(by_category, dict):
            raise ResultIntegrityError(f"summary.factors.{factor}.by_category must be an object")
        for category in sorted(set(category_by_id.values())):
            category_outcomes = [
                outcome
                for question_id, outcome in outcomes.items()
                if category_by_id[question_id] == category
            ]
            by_category[category] = _rate(sum(category_outcomes), len(category_outcomes))
    return revised


def _paired_payload(
    left: Mapping[str, bool], right: Mapping[str, bool], *, question_ids: Sequence[str]
) -> dict[str, Any]:
    left_subset = {question_id: int(left[question_id]) for question_id in question_ids}
    right_subset = {question_id: int(right[question_id]) for question_id in question_ids}
    only_left, only_right, probability = mcnemar_exact(left_subset, right_subset)
    return {
        "n": len(question_ids),
        "baseline_correct": sum(left_subset.values()),
        "candidate_correct": sum(right_subset.values()),
        "baseline_only": only_left,
        "candidate_only": only_right,
        "mcnemar_exact_two_sided_p": round(probability, 10),
    }


def build_analysis_audit(repo_root: Path) -> dict[str, Any]:
    """Recompute the committed post-hoc audit from frozen local evidence only."""
    root = repo_root.resolve()
    paths = RepoPaths(root=root)
    runs_dir = paths.runs
    gold_path = paths.locked_gold
    summary_path = paths.summary_json
    gate_path = paths.go_no_go_json
    lock_path = paths.protocol_lock_json

    gold_by_id = _gold_by_id(gold_path)
    summary = _json_object(summary_path)
    gate = _json_object(gate_path)
    recorded_by_factor: dict[str, dict[str, bool]] = {}
    runtime_by_factor: dict[str, dict[str, bool]] = {}
    literal_by_factor: dict[str, dict[str, bool]] = {}
    scores_by_factor: dict[str, dict[str, AnswerScore]] = {}
    payloads_by_factor: dict[str, list[dict[str, Any]]] = {}
    runtime_mismatches: list[dict[str, Any]] = []
    literal_differences: list[dict[str, Any]] = []
    run_hashes: dict[str, str] = {}

    for factor in FACTOR_IDS:
        records_path = runs_dir / factor / "records.jsonl"
        run_hashes[factor] = sha256_text_file(records_path)
        recorded: dict[str, bool] = {}
        runtime: dict[str, bool] = {}
        literal: dict[str, bool] = {}
        factor_payloads = _jsonl_objects(records_path)
        scores: dict[str, AnswerScore] = {}
        for payload in factor_payloads:
            question_id = payload.get("question_id")
            if not isinstance(question_id, str) or question_id not in gold_by_id:
                raise ResultIntegrityError(f"{factor}: unknown question id {question_id!r}")
            if question_id in recorded:
                raise ResultIntegrityError(f"{factor}: duplicate question id {question_id}")
            recorded_correct = payload.get("correct")
            if not isinstance(recorded_correct, bool):
                raise ResultIntegrityError(f"{factor}/{question_id}: correct must be boolean")
            rescored = _regrade(payload, gold_by_id[question_id])
            rescored_payload = rescored.to_json()
            committed_score = payload.get("score")
            if (
                committed_score != rescored_payload
                or recorded_correct != rescored.correct
                or payload.get("refused") != rescored.refused
            ):
                runtime_mismatches.append(
                    {
                        "factor": factor,
                        "question_id": question_id,
                        "recorded_correct": recorded_correct,
                        "recorded_score": committed_score,
                        "regraded_score": rescored_payload,
                    }
                )
            recorded[question_id] = recorded_correct
            runtime[question_id] = rescored.correct
            scores[question_id] = rescored
            literal_correct = protocol_literal_correct(rescored)
            literal[question_id] = literal_correct
            if recorded_correct != literal_correct:
                literal_differences.append(
                    {
                        "factor": factor,
                        "question_id": question_id,
                        "recorded_correct": recorded_correct,
                        "protocol_literal_correct": literal_correct,
                    }
                )
        expected_ids = set(gold_by_id)
        if set(recorded) != expected_ids:
            missing = sorted(expected_ids - set(recorded))
            extra = sorted(set(recorded) - expected_ids)
            raise ResultIntegrityError(
                f"{factor}: question set differs; missing={missing}, extra={extra}"
            )
        recorded_by_factor[factor] = recorded
        runtime_by_factor[factor] = runtime
        literal_by_factor[factor] = literal
        scores_by_factor[factor] = scores
        payloads_by_factor[factor] = factor_payloads

    literal_summary = _literal_summary(
        summary,
        literal_by_factor,
        {question_id: gold.question_type for question_id, gold in gold_by_id.items()},
    )
    recorded_verdict = gate.get("verdict")
    if not isinstance(recorded_verdict, str):
        raise ResultIntegrityError("GO_NO_GO.verdict must be text")
    literal_verdict = decide(evaluate(literal_summary))
    factor_rows = {
        factor: {
            "n": len(recorded_by_factor[factor]),
            "recorded_correct": sum(recorded_by_factor[factor].values()),
            "runtime_regraded_correct": sum(runtime_by_factor[factor].values()),
            "protocol_literal_correct": sum(literal_by_factor[factor].values()),
        }
        for factor in FACTOR_IDS
    }

    all_ids = sorted(gold_by_id)
    hard_ids = sorted(
        question_id
        for question_id, gold in gold_by_id.items()
        if gold.question_type in HARD_CATEGORIES
    )
    gold_records = list(gold_by_id.values())
    pending = sorted(record.question_id for record in gold_records if not record.is_trustworthy)
    return {
        "schema_version": "1.0",
        "scope": {
            "kind": "posthoc_protocol_reconciliation",
            "model_rerun": False,
            "changes_frozen_artifacts": False,
            "official_summary_preserved": True,
        },
        "inputs": {
            "protocol_lock_sha256": sha256_text_file(lock_path),
            "locked_gold_sha256": sha256_text_file(gold_path),
            "summary_sha256": sha256_text_file(summary_path),
            "go_no_go_sha256": sha256_text_file(gate_path),
            "run_records_sha256": run_hashes,
            "locked_run_code_commit": summary.get("code_commit"),
        },
        "score_reconciliation": {
            "runtime_rule": "numeric_ok for numeric; exact_match for text; correct refusal",
            "protocol_literal_rule": (
                "numeric_ok for numeric; exact_match OR token_f1 >= 0.8 for text; correct refusal"
            ),
            "factors": factor_rows,
            "runtime_regrade_mismatches": runtime_mismatches,
            "protocol_literal_differences": literal_differences,
        },
        "paired_comparisons": {
            "recorded": {
                "overall": _paired_payload(
                    recorded_by_factor[BASELINE_FACTOR],
                    recorded_by_factor[CANDIDATE_FACTOR],
                    question_ids=all_ids,
                ),
                "pooled_hard": _paired_payload(
                    recorded_by_factor[BASELINE_FACTOR],
                    recorded_by_factor[CANDIDATE_FACTOR],
                    question_ids=hard_ids,
                ),
            },
            "protocol_literal": {
                "overall": _paired_payload(
                    literal_by_factor[BASELINE_FACTOR],
                    literal_by_factor[CANDIDATE_FACTOR],
                    question_ids=all_ids,
                ),
                "pooled_hard": _paired_payload(
                    literal_by_factor[BASELINE_FACTOR],
                    literal_by_factor[CANDIDATE_FACTOR],
                    question_ids=hard_ids,
                ),
            },
        },
        "candidate_secondary_metrics": _candidate_secondary_metrics(
            payloads_by_factor[CANDIDATE_FACTOR],
            gold_by_id,
            scores_by_factor[CANDIDATE_FACTOR],
        ),
        "metric_coverage": _metric_coverage(),
        "gold_audit": {
            "composition": composition(gold_records),
            "pending_question_ids": pending,
            "independent_blind_final_audit": False,
        },
        "reproducibility": {
            "clean_clone": "committed evaluation and post-hoc analysis reproducible",
            "source_data": ("manifests and SHA-256 only; third-party raw bytes not redistributed"),
            "ingestion": "requires reacquisition of exact third-party source bytes",
            "model_run": "requires local model weights and uncommitted derived indexes",
            "end_to_end": "not demonstrated from a clean clone",
        },
        "conclusion": {
            "recorded_verdict": recorded_verdict,
            "protocol_literal_verdict": literal_verdict,
            "baseline_recorded_correct": factor_rows[BASELINE_FACTOR]["recorded_correct"],
            "baseline_protocol_literal_correct": factor_rows[BASELINE_FACTOR][
                "protocol_literal_correct"
            ],
            "candidate_correct": factor_rows[CANDIDATE_FACTOR]["protocol_literal_correct"],
            "negative_result_preserved": literal_verdict == "NO_GO",
        },
    }


def verify_committed_analysis_audit(
    repo_root: Path, *, audit_path: Path | None = None
) -> tuple[str, ...]:
    """Require the committed audit JSON to equal a fresh deterministic recomputation."""
    destination = audit_path or RepoPaths(root=repo_root).analysis_audit_json
    try:
        committed = _json_object(destination)
        expected = build_analysis_audit(repo_root)
    except ResultIntegrityError as exc:
        return (str(exc),)
    if committed != expected:
        return ("committed analysis_audit.json differs from deterministic recomputation",)
    return ()
