"""Post-hoc analysis must expose protocol/runtime differences without rewriting the run."""

from __future__ import annotations

import importlib
import json
import shutil
from pathlib import Path
from types import ModuleType

from typer.testing import CliRunner

from twfi.eval.answers import AnswerScore


def analysis_audit() -> ModuleType:
    """Import inside tests so the first TDD run fails as RED rather than at collection."""
    return importlib.import_module("twfi.eval.analysis_audit")


def test_protocol_literal_text_rule_accepts_token_f1_at_threshold() -> None:
    score = AnswerScore(
        exact=False,
        f1=0.8,
        numeric=None,
        unit=None,
        period=True,
        refused=False,
        should_refuse=False,
    )

    assert analysis_audit().protocol_literal_correct(score)


def test_protocol_literal_rule_keeps_numeric_and_refusal_semantics() -> None:
    numeric_failure = AnswerScore(
        exact=True,
        f1=1.0,
        numeric=False,
        unit=True,
        period=True,
        refused=False,
        should_refuse=False,
    )
    correct_refusal = AnswerScore(
        exact=False,
        f1=0.0,
        numeric=None,
        unit=None,
        period=False,
        refused=True,
        should_refuse=True,
    )

    assert not analysis_audit().protocol_literal_correct(numeric_failure)
    assert analysis_audit().protocol_literal_correct(correct_refusal)


def test_locked_predictions_regrade_to_the_recorded_runtime_scores(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)

    assert payload["score_reconciliation"]["runtime_regrade_mismatches"] == []


def test_flat_correct_drift_is_reported_as_a_runtime_regrade_mismatch(
    repo_root: Path, tmp_path: Path
) -> None:
    shutil.copytree(
        repo_root / "data" / "evaluation" / "locked", tmp_path / "data" / "evaluation" / "locked"
    )
    shutil.copytree(repo_root / "results", tmp_path / "results")
    records_path = tmp_path / "results" / "runs" / "F0" / "records.jsonl"
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["correct"] = not rows[0]["correct"]
    records_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8"
    )

    payload = analysis_audit().build_analysis_audit(tmp_path)

    assert (
        payload["score_reconciliation"]["runtime_regrade_mismatches"][0]["question_id"]
        == "LOCK-0001"
    )


def test_protocol_literal_reconciliation_names_every_changed_record(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)
    reconciliation = payload["score_reconciliation"]

    expected = {
        "F0": (17, 18),
        "F1": (15, 16),
        "F2": (14, 16),
        "F3": (19, 19),
        "F4": (19, 19),
        "F5": (20, 20),
        "F6": (18, 18),
        "F7": (6, 6),
    }
    actual = {
        factor: (row["recorded_correct"], row["protocol_literal_correct"])
        for factor, row in reconciliation["factors"].items()
    }
    assert actual == expected
    assert reconciliation["protocol_literal_differences"] == [
        {
            "factor": "F0",
            "question_id": "LOCK-0019",
            "recorded_correct": False,
            "protocol_literal_correct": True,
        },
        {
            "factor": "F1",
            "question_id": "LOCK-0019",
            "recorded_correct": False,
            "protocol_literal_correct": True,
        },
        {
            "factor": "F2",
            "question_id": "LOCK-0019",
            "recorded_correct": False,
            "protocol_literal_correct": True,
        },
        {
            "factor": "F2",
            "question_id": "LOCK-0022",
            "recorded_correct": False,
            "protocol_literal_correct": True,
        },
    ]


def test_reconciliation_preserves_the_registered_no_go(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)

    conclusion = payload["conclusion"]
    assert conclusion["recorded_verdict"] == "NO_GO"
    assert conclusion["protocol_literal_verdict"] == "NO_GO"
    assert conclusion["candidate_correct"] == 6
    assert conclusion["baseline_recorded_correct"] == 17
    assert conclusion["baseline_protocol_literal_correct"] == 18


def test_gold_audit_discloses_every_pending_model_chosen_question(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)

    assert payload["gold_audit"]["composition"] == {
        "records": 33,
        "fully_human": 19,
        "answer_model_drafted": 7,
        "question_model_chosen": 9,
        "needs_audit": 14,
        "audited": 10,
        "trustworthy": 29,
    }
    assert payload["gold_audit"]["pending_question_ids"] == [
        "LOCK-0025",
        "LOCK-0026",
        "LOCK-0030",
        "LOCK-0031",
    ]


def test_candidate_secondary_metrics_are_recomputed_from_question_records(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)
    metrics = payload["candidate_secondary_metrics"]

    assert metrics["retrieval"] == {
        "recall_at_5": {"n": 33, "passed": 25, "rate": 0.757576},
        "mrr_at_10": {"n": 33, "mean": 0.458415},
        "complete_evidence_at_5": {"n": 33, "passed": 19, "rate": 0.575758},
        "multi_target_complete_at_5": {"n": 8, "passed": 1, "rate": 0.125},
    }
    assert metrics["answer"] == {
        "exact_match": {"n": 29, "passed": 4, "rate": 0.137931},
        "token_f1_mean": {"n": 29, "mean": 0.197202},
        "numeric_ok": {"n": 26, "passed": 4, "rate": 0.153846},
        "unit_ok": {"n": 16, "passed": 2, "rate": 0.125},
        "period_ok": {"n": 29, "passed": 1, "rate": 0.034483},
        "refusal_precision": {"n": 16, "passed": 1, "rate": 0.0625},
        "refusal_recall": {"n": 4, "passed": 1, "rate": 0.25},
    }


def test_metric_coverage_does_not_invent_missing_citation_grades(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)
    coverage = {row["metric"]: row["status"] for row in payload["metric_coverage"]}

    assert coverage["citation_validity"] == "reaggregated_runtime_verdict"
    for metric in (
        "citation_precision",
        "citation_recall",
        "citation_page_correctness",
        "citation_bbox_or_row_validity",
    ):
        assert coverage[metric] == "not_collected"
    assert coverage["route_confusion_matrix"] == "recomputed"
    assert coverage["mcnemar_exact"] == "recomputed"


def test_reproducibility_tiers_keep_clean_clone_claim_narrow(repo_root: Path) -> None:
    payload = analysis_audit().build_analysis_audit(repo_root)

    assert payload["reproducibility"]["clean_clone"] == (
        "committed evaluation and post-hoc analysis reproducible"
    )
    assert payload["reproducibility"]["source_data"] == (
        "manifests and SHA-256 only; third-party raw bytes not redistributed"
    )
    assert payload["reproducibility"]["end_to_end"] == "not demonstrated from a clean clone"


def test_committed_analysis_audit_is_exact_recomputation(repo_root: Path) -> None:
    destination = repo_root / "results" / "feasibility" / "analysis_audit.json"
    committed = json.loads(destination.read_text(encoding="utf-8"))

    assert committed == analysis_audit().build_analysis_audit(repo_root)
    assert analysis_audit().verify_committed_analysis_audit(repo_root) == ()


def test_analysis_audit_cli_verifies_without_writing() -> None:
    command = importlib.import_module("scripts.verify_analysis_audit").app

    result = CliRunner().invoke(command)

    assert result.exit_code == 0, result.output
    assert "protocol-literal" in result.output
