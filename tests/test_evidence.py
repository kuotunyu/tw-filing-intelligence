"""Committed evidence must reproduce without trusting the prose that describes it."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from types import ModuleType
from typing import Any

import pytest
from typer.testing import CliRunner


def evidence() -> ModuleType:
    """Import inside each test so a missing implementation is a RED failure, not collection."""
    return importlib.import_module("twfi.eval.evidence")


def _json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _committed(repo_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    feasibility = repo_root / "results" / "feasibility"
    return _json(feasibility / "summary.json"), _json(feasibility / "GO_NO_GO.json")


def test_windows_path_is_recorded_as_repository_relative_posix() -> None:
    actual = evidence().repository_relative_path(
        PureWindowsPath(r"C:\work\twfi\results\feasibility\summary.json"),
        PureWindowsPath(r"C:\work\twfi"),
    )
    assert actual == "results/feasibility/summary.json"


def test_posix_path_is_recorded_as_repository_relative_posix() -> None:
    actual = evidence().repository_relative_path(
        PurePosixPath("/work/twfi/results/runs"), PurePosixPath("/work/twfi")
    )
    assert actual == "results/runs"


def test_verify_results_accepts_repository_relative_cli_inputs() -> None:
    command = importlib.import_module("scripts.verify_results").app
    result = CliRunner().invoke(
        command,
        [
            "--summary",
            "results/feasibility/summary.json",
            "--raw",
            "results/runs",
            "--lock",
            "results/feasibility/protocol_lock.json",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output


def test_path_outside_repository_is_refused() -> None:
    with pytest.raises(ValueError, match="outside repository"):
        evidence().repository_relative_path(
            PureWindowsPath(r"C:\Users\analyst\summary.json"),
            PureWindowsPath(r"C:\work\twfi"),
        )


def test_exact_committed_gate_artifact_matches_recomputation(repo_root: Path) -> None:
    summary, committed = _committed(repo_root)
    assert evidence().gate_artifact_problems(summary, committed) == ()


def test_metric_drift_fails_gate_equivalence(repo_root: Path) -> None:
    summary, committed = _committed(repo_root)
    changed = copy.deepcopy(summary)
    changed["factors"]["F7"]["overall_accuracy"]["correct"] = 7
    assert any(
        "gates" in problem for problem in evidence().gate_artifact_problems(changed, committed)
    )


def test_verdict_drift_fails_gate_equivalence(repo_root: Path) -> None:
    summary, committed = _committed(repo_root)
    changed = copy.deepcopy(committed)
    changed["verdict"] = "GO"
    assert any(
        "verdict" in problem for problem in evidence().gate_artifact_problems(summary, changed)
    )


def test_protocol_hash_drift_fails_gate_equivalence(repo_root: Path) -> None:
    summary, committed = _committed(repo_root)
    changed = copy.deepcopy(committed)
    changed["protocol_lock_sha256"] = "0" * 64
    assert any(
        "protocol lock" in problem
        for problem in evidence().gate_artifact_problems(summary, changed)
    )


@pytest.mark.parametrize(
    "unsafe",
    [
        r"C:\Users\analyst\twfi\results\runs",
        "C:/Users/analyst/twfi/results/runs",
        "/home/analyst/twfi/results/runs",
        "../results/runs",
    ],
)
def test_machine_specific_or_escaping_path_fails_verification(unsafe: str) -> None:
    expected = {
        "summary": "results/feasibility/summary.json",
        "raw": "results/runs",
        "records_per_run": {"F0": 33},
        "reproducible": True,
        "problems": [],
    }
    committed = dict(expected, raw=unsafe)
    assert any(
        "repository-relative POSIX" in problem
        for problem in evidence().results_artifact_problems(expected, committed)
    )


def test_missing_committed_gate_artifact_fails_repository_verifier(
    repo_root: Path, tmp_path: Path
) -> None:
    missing = tmp_path / "missing-GO_NO_GO.json"
    problems = evidence().verify_committed_evidence(repo_root, gate_path=missing)
    assert any("missing committed gate artifact" in problem for problem in problems)


def test_committed_repository_evidence_chain_is_equivalent(repo_root: Path) -> None:
    assert evidence().verify_committed_evidence(repo_root) == ()
