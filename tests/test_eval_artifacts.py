"""The locked runner's artifacts must reproduce without hand-edited numbers."""

from __future__ import annotations

import datetime as dt
from typing import Any

from twfi.eval.artifacts import (
    build_error_analysis,
    build_summary,
    graded_record,
    nearest_rank_p95,
)
from twfi.eval.gold import CompanyRef, EvidenceRef, GoldRecord
from twfi.eval.results import verify
from twfi.protocol import FACTOR_IDS, ROUTE_BY_QUESTION_TYPE

LOCK = "a" * 64
RESOURCES = {
    "retrieval_p95_s": 1.2,
    "generation_p95_s": 40.0,
    "vram_peak_gb": 20.09,
}


def _gold(question_type: str = "table_cell", *, answerable: bool = True) -> GoldRecord:
    return GoldRecord(
        question_id="LOCK-0001",
        question_type=question_type,  # type: ignore[arg-type]
        question="台積電的值是多少？",
        answer="42" if answerable else None,
        answerable=answerable,
        company=CompanyRef("台積電", "2330"),
        period="FY2024",
        source_document=("2330-FY2024-FS",),
        page_numbers=(1,),
        required_evidence=(EvidenceRef("page", "2330-FY2024-FS#p1"),),
        answer_provenance="human_read_pdf",
        annotator="human",
        annotated_at=dt.date(2026, 8, 1),
    )


def _ladder_row(*, refused: bool = False) -> dict[str, Any]:
    return {
        "factor": "F7",
        "question_id": "LOCK-0001",
        "question_type": "table_cell",
        "predicted": "無法回答" if refused else "42",
        "cited_ok": None if refused else True,
        "route": {"route": "chart", "reason": "printed", "confidence": 0.6},
        "handled_route": "chart",
        "score": {"correct": True, "refused": refused},
        "retrieval": {"recall_at_5": True, "seconds": 0.2},
        "generation": {"seconds": 1.0, "error": ""},
    }


def _official(
    question_id: str,
    category: str,
    factor: str,
    *,
    route: str | None = None,
    correct: bool = True,
) -> dict[str, Any]:
    unanswerable = category == "unanswerable"
    actual = route or ROUTE_BY_QUESTION_TYPE[category]
    return {
        "question_id": question_id,
        "factor": factor,
        "category": category,
        "answerable": not unanswerable,
        "gold_route": ROUTE_BY_QUESTION_TYPE[category],
        "route": actual,
        "handled_route": actual,
        "correct": correct,
        "refused": unanswerable and correct,
        "cited_ok": None if unanswerable else correct,
    }


def test_ladder_row_becomes_the_required_graded_record() -> None:
    record = graded_record(_ladder_row(), _gold())

    assert record["category"] == "table_cell"
    assert record["answerable"] is True
    assert record["gold_route"] == "chart"
    assert record["route"] == "chart"
    assert record["correct"] is True
    assert record["cited_ok"] is True


def test_a_refusal_is_recorded_as_the_pipeline_effective_route() -> None:
    record = graded_record(_ladder_row(refused=True), _gold("unanswerable", answerable=False))

    assert record["route"] == "unanswerable"


def test_summary_recomputes_exactly_from_every_factor_and_probe() -> None:
    runs: dict[str, list[dict[str, Any]]] = {}
    for factor in FACTOR_IDS:
        runs[factor] = [
            _official("LOCK-0001", "table_cell", factor),
            _official("LOCK-0002", "numeric_calculation", factor),
            _official("LOCK-0003", "unanswerable", factor),
        ]
    runs["probes"] = [
        {
            **_official("PROBE-0001", "unanswerable", "F7"),
            "category": "probe",
            "gold_route": "unanswerable",
            "answerable": False,
        }
    ]

    summary = build_summary(
        runs,
        protocol_lock_sha256=LOCK,
        resources=RESOURCES,
        data_reproducible=True,
    )

    assert verify(summary, runs, expected_lock_sha256=LOCK, resources=RESOURCES) == ()
    assert summary["factors"]["F7"]["overall_accuracy"]["correct"] == 3
    assert summary["unanswerable"]["rate"] == 0.0
    assert len(summary["unanswerable"]["ci95"]) == 2
    assert summary["checks"]["results_reproducible"] is False


def test_numeric_metric_uses_the_handler_even_when_its_refusal_ends_unanswerable() -> None:
    runs: dict[str, list[dict[str, Any]]] = {}
    for factor in FACTOR_IDS:
        runs[factor] = [
            _official("LOCK-0001", "numeric_calculation", factor),
            _official("LOCK-0002", "unanswerable", factor),
        ]
    runs["F7"][0].update(
        {"route": "unanswerable", "handled_route": "numeric", "correct": False, "refused": True}
    )
    runs["probes"] = [
        {
            **_official("PROBE-0001", "unanswerable", "F7"),
            "category": "probe",
            "answerable": False,
        }
    ]

    summary = build_summary(
        runs,
        protocol_lock_sha256=LOCK,
        resources=RESOURCES,
        data_reproducible=True,
    )

    assert summary["numeric_route_accuracy"]["n"] == 1
    assert summary["numeric_route_accuracy"]["correct"] == 0


def test_p95_uses_a_conservative_nearest_rank() -> None:
    assert nearest_rank_p95([float(value) for value in range(1, 21)]) == 19.0
    assert nearest_rank_p95([3.25]) == 3.25


def test_error_analysis_preserves_all_actionable_failure_buckets() -> None:
    broken = _official("LOCK-0001", "table_cell", "F7", route="narrative", correct=False)
    broken.update(
        {
            "refused": True,
            "cited_ok": False,
            "retrieval": {"recall_at_5": False},
            "generation": {"error": "timeout"},
        }
    )

    analysis = build_error_analysis([broken])

    assert analysis[0]["question_id"] == "LOCK-0001"
    assert set(analysis[0]["buckets"]) == {
        "generation_error",
        "incorrect_refusal",
        "route_error",
        "citation_invalid",
        "retrieval_miss",
    }
