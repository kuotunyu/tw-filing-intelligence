"""The locked evaluation is a guarded one-shot operation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from twfi.errors import EvaluationError
from twfi.eval.locked_run import (
    begin_locked_run,
    locked_request_problems,
    resource_measurements,
)
from twfi.protocol import FACTOR_IDS


def test_only_the_complete_frozen_configuration_is_a_locked_run() -> None:
    assert (
        locked_request_problems(
            factors=FACTOR_IDS,
            limit=0,
            prompt_variant="strict",
            numeric_db="numeric_broad.duckdb",
        )
        == []
    )


def test_partial_or_tuned_locked_requests_are_refused() -> None:
    problems = locked_request_problems(
        factors=("F7",),
        limit=1,
        prompt_variant="permissive",
        numeric_db="numeric.duckdb",
    )

    assert len(problems) == 4
    assert any("F0..F7" in problem for problem in problems)
    assert any("--limit" in problem for problem in problems)
    assert any("prompt" in problem for problem in problems)
    assert any("numeric_broad" in problem for problem in problems)


def test_locked_run_marker_is_created_exclusively(tmp_path: Path) -> None:
    marker = tmp_path / "locked_run_started.json"
    payload = {"protocol_lock_sha256": "a" * 64, "factors": list(FACTOR_IDS)}

    begin_locked_run(marker, payload)

    assert json.loads(marker.read_text(encoding="utf-8")) == payload
    with pytest.raises(EvaluationError, match="already started"):
        begin_locked_run(marker, payload)


def test_resources_use_candidate_latency_and_measured_own_vram() -> None:
    rows = [
        {
            "retrieval": {"seconds": float(value)},
            "generation": {"seconds": float(value * 10)},
        }
        for value in range(1, 21)
    ]

    measured = resource_measurements(rows, {"vram_own_footprint_gb": 20.09})

    assert measured == {
        "retrieval_p95_s": 19.0,
        "generation_p95_s": 190.0,
        "vram_peak_gb": 20.09,
    }
