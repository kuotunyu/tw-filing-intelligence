"""Cross-check committed research evidence without running a model or changing a result."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

from twfi.errors import ProtocolLockError, ResultIntegrityError
from twfi.eval.gates import GateOutcome, decide, evaluate
from twfi.eval.protocol_lock import assert_lock_valid
from twfi.eval.results import load_artifacts, verify
from twfi.io.hashing import sha256_text_file
from twfi.paths import RepoPaths

__all__ = [
    "build_gate_payload",
    "build_results_artifact",
    "gate_artifact_problems",
    "repository_relative_path",
    "results_artifact_problems",
    "verify_committed_evidence",
]


def repository_relative_path(path: PurePath, root: PurePath) -> str:
    """Return a portable POSIX path, refusing to serialize anything outside ``root``."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{path} is outside repository {root}") from exc
    return PurePosixPath(*relative.parts).as_posix()


def build_results_artifact(
    *,
    summary_path: PurePath,
    raw_dir: PurePath,
    repo_root: PurePath,
    records_per_run: Mapping[str, int],
    problems: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build the portable record written by ``verify_results``."""
    return {
        "summary": repository_relative_path(summary_path, repo_root),
        "raw": repository_relative_path(raw_dir, repo_root),
        "records_per_run": dict(sorted(records_per_run.items())),
        "reproducible": not problems,
        "problems": list(problems),
    }


def build_gate_payload(
    summary: Mapping[str, Any], outcomes: Sequence[GateOutcome] | None = None
) -> dict[str, object]:
    """Build the verdict artifact with the frozen evaluator used by ``run_gate``."""
    evaluated = tuple(outcomes) if outcomes is not None else evaluate(summary)
    return {
        "verdict": decide(evaluated),
        "protocol_lock_sha256": summary.get("protocol_lock_sha256"),
        "gates": [outcome.to_json() for outcome in evaluated],
    }


def gate_artifact_problems(
    summary: Mapping[str, Any],
    committed: Mapping[str, Any],
    *,
    expected_verdict: str = "NO_GO",
) -> tuple[str, ...]:
    """Name every way a committed gate artifact differs from fresh recomputation."""
    expected = build_gate_payload(summary)
    problems: list[str] = []
    if expected["verdict"] != expected_verdict:
        problems.append(
            f"recomputed verdict is {expected['verdict']!r}, expected {expected_verdict!r}"
        )
    if committed.get("verdict") != expected["verdict"]:
        problems.append(
            "committed verdict differs from recomputation: "
            f"{committed.get('verdict')!r} != {expected['verdict']!r}"
        )
    if committed.get("protocol_lock_sha256") != expected["protocol_lock_sha256"]:
        problems.append("committed protocol lock hash differs from summary.json")
    if committed.get("gates") != expected["gates"]:
        problems.append("committed metric-derived gates differ from frozen gate recomputation")
    if dict(committed) != expected and not problems:
        problems.append("committed gate artifact is not the exact recomputed payload")
    return tuple(problems)


def _is_repository_relative_posix(value: object) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    posix = PurePosixPath(value)
    windows = PureWindowsPath(value)
    return (
        not posix.is_absolute()
        and not windows.is_absolute()
        and not windows.drive
        and ".." not in posix.parts
    )


def results_artifact_problems(
    expected: Mapping[str, object], committed: Mapping[str, Any]
) -> tuple[str, ...]:
    """Require safe path fields and byte-for-byte-equivalent JSON content."""
    problems: list[str] = []
    for field in ("summary", "raw"):
        if not _is_repository_relative_posix(committed.get(field)):
            problems.append(
                f"results_verification.{field} must be a repository-relative POSIX path"
            )
    if dict(committed) != dict(expected):
        problems.append("committed results_verification artifact differs from raw recomputation")
    return tuple(problems)


def _read_json_object(path: Path, label: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{label} is unreadable: {exc}"
    if not isinstance(payload, dict):
        return None, f"{label} must contain a JSON object"
    return payload, None


def verify_committed_evidence(
    repo_root: Path,
    *,
    gate_path: Path | None = None,
    verification_path: Path | None = None,
    expected_verdict: str = "NO_GO",
) -> tuple[str, ...]:
    """Verify lock -> raw recomputation -> committed artifacts using only local files."""
    paths = RepoPaths(root=repo_root)
    gate_file = gate_path or paths.go_no_go_json
    results_file = verification_path or paths.feasibility / "results_verification.json"
    problems: list[str] = []

    required_files = (
        (paths.summary_json, "missing summary artifact"),
        (paths.protocol_lock_json, "missing protocol lock"),
        (gate_file, "missing committed gate artifact"),
        (results_file, "missing committed results verification artifact"),
    )
    for path, label in required_files:
        if not path.is_file():
            problems.append(f"{label}: {path}")
    if not paths.runs.is_dir():
        problems.append(f"missing committed raw run directory: {paths.runs}")
    if problems:
        return tuple(problems)

    try:
        assert_lock_valid(repo_root, paths.protocol_lock_json)
    except ProtocolLockError as exc:
        problems.append(f"frozen protocol validation failed: {exc}")

    summary, error = _read_json_object(paths.summary_json, "summary artifact")
    if error is not None or summary is None:
        return (*problems, error or "summary artifact is unreadable")
    committed_gate, error = _read_json_object(gate_file, "committed gate artifact")
    if error is not None or committed_gate is None:
        return (*problems, error or "committed gate artifact is unreadable")
    committed_results, error = _read_json_object(
        results_file, "committed results verification artifact"
    )
    if error is not None or committed_results is None:
        return (*problems, error or "committed results verification artifact is unreadable")

    try:
        artifacts = load_artifacts(paths.runs)
    except ResultIntegrityError as exc:
        return (*problems, f"raw run artifacts are unreadable: {exc}")
    raw_problems = verify(
        summary,
        artifacts.runs,
        expected_lock_sha256=sha256_text_file(paths.protocol_lock_json),
        resources=artifacts.resources,
    )
    problems.extend(f"raw-to-summary recomputation failed: {problem}" for problem in raw_problems)

    expected_results = build_results_artifact(
        summary_path=paths.summary_json,
        raw_dir=paths.runs,
        repo_root=repo_root,
        records_per_run={run: len(records) for run, records in artifacts.runs.items()},
        problems=[problem.to_json() for problem in raw_problems],
    )
    problems.extend(results_artifact_problems(expected_results, committed_results))
    problems.extend(
        gate_artifact_problems(summary, committed_gate, expected_verdict=expected_verdict)
    )
    return tuple(problems)
