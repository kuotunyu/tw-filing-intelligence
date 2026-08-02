"""Guards and measurements for the protocol's single locked evaluation."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from twfi.answer.prompt import DEFAULT_VARIANT
from twfi.errors import EvaluationError, ResultIntegrityError
from twfi.eval.artifacts import nearest_rank_p95
from twfi.protocol import FACTOR_IDS

__all__ = ["begin_locked_run", "locked_request_problems", "resource_measurements"]

LOCKED_NUMERIC_DB = "numeric_broad.duckdb"


def locked_request_problems(
    *,
    factors: Sequence[str],
    limit: int,
    prompt_variant: str,
    numeric_db: str,
    depth: int,
    rerank_device: str,
) -> list[str]:
    """Name every way a requested run differs from the frozen full ladder."""
    problems: list[str] = []
    if tuple(factors) != FACTOR_IDS:
        problems.append("the locked run must execute F0..F7 once, in the registered order")
    if limit != 0:
        problems.append("--limit is forbidden for the locked run; all locked questions are graded")
    if prompt_variant != DEFAULT_VARIANT:
        problems.append(f"the locked prompt must be {DEFAULT_VARIANT!r}, got {prompt_variant!r}")
    if numeric_db != LOCKED_NUMERIC_DB:
        problems.append(
            f"the locked numeric route must use {LOCKED_NUMERIC_DB}, got {numeric_db!r}"
        )
    if depth != 100:
        problems.append(f"the locked pre-fusion fetch depth must be 100, got {depth}")
    if rerank_device != "cuda":
        problems.append(f"the locked reranker must run on cuda, got {rerank_device!r}")
    return problems


def begin_locked_run(marker: Path, payload: Mapping[str, Any]) -> None:
    """Create the irreversible attempt marker, refusing if any attempt already exists."""
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        with marker.open("x", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except FileExistsError as exc:
        raise EvaluationError(
            f"the locked run already started; marker exists at {marker}. Do not rerun a subset "
            "or delete the marker to conceal an interrupted attempt."
        ) from exc


def resource_measurements(
    candidate_rows: Sequence[Mapping[str, Any]], budget: Mapping[str, Any]
) -> dict[str, float]:
    """Compute G10 latency p95 and map the measured all-model VRAM footprint."""
    retrieval: list[float] = []
    generation: list[float] = []
    for index, row in enumerate(candidate_rows):
        retrieval_block = row.get("retrieval")
        generation_block = row.get("generation")
        if not isinstance(retrieval_block, Mapping) or not isinstance(generation_block, Mapping):
            raise ResultIntegrityError(f"candidate row {index} has no retrieval/generation data")
        try:
            retrieval.append(float(retrieval_block["seconds"]))
            generation.append(float(generation_block["seconds"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResultIntegrityError(f"candidate row {index} has invalid latency data") from exc
    vram = budget.get("vram_own_footprint_gb")
    if isinstance(vram, bool) or not isinstance(vram, (int, float)):
        raise ResultIntegrityError(
            "resource_budget.json has no numeric vram_own_footprint_gb measurement"
        )
    return {
        "retrieval_p95_s": round(nearest_rank_p95(retrieval), 6),
        "generation_p95_s": round(nearest_rank_p95(generation), 6),
        "vram_peak_gb": float(vram),
    }
