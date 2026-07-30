"""Freezing and verifying the pre-registered protocol.

The point of this module is to make "we changed the rules after seeing the
numbers" *detectable*. Freezing records a SHA-256 for every artifact that the
protocol says must not change; ``pytest`` then re-verifies those digests on every
run, so an edit to the protocol, the locked gold set, the data manifests, or the
model pins fails the suite instead of silently producing nicer results.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from twfi.errors import ProtocolLockError
from twfi.io.hashing import sha256_file, sha256_text_file

__all__ = [
    "DigestMode",
    "LockEntry",
    "ProtocolLock",
    "LOCKED_ARTIFACTS",
    "compute_digest",
    "build_lock",
    "load_lock",
    "verify_lock",
    "assert_lock_valid",
]

DigestMode = Literal["text", "bytes"]

#: Repository-relative artifacts covered by the freeze, with the digest mode used
#: for each. ``optional`` entries may be absent at freeze time (for example when a
#: manifest is not part of the run), but once recorded they must never change.
LOCKED_ARTIFACTS: tuple[tuple[str, DigestMode, bool], ...] = (
    ("docs/FEASIBILITY_PROTOCOL.md", "text", False),
    ("data/evaluation/locked/gold.jsonl", "text", False),
    ("data/evaluation/locked/probes.jsonl", "text", False),
    ("data/manifests/documents.yaml", "text", False),
    ("data/manifests/structured.yaml", "text", False),
    # The record of what was actually acquired, including every SHA-256. Freezing
    # it is what makes G1 ("results reconstructible from raw artifacts") checkable
    # rather than asserted.
    ("data/manifests/acquisition.lock.yaml", "text", False),
    ("configs/models.lock.json", "text", False),
)


@dataclass(frozen=True, slots=True)
class LockEntry:
    """One frozen artifact."""

    path: str
    sha256: str
    mode: DigestMode

    def to_json(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256, "mode": self.mode}


@dataclass(frozen=True, slots=True)
class ProtocolLock:
    """The frozen state of the protocol."""

    protocol_version: str
    frozen_at: str
    entries: tuple[LockEntry, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "protocol_version": self.protocol_version,
            "frozen_at": self.frozen_at,
            "entries": [entry.to_json() for entry in self.entries],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_json(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def compute_digest(root: Path, relative_path: str, mode: DigestMode) -> str:
    """Return the digest of ``root / relative_path`` using ``mode``.

    Raises:
        ProtocolLockError: If the file does not exist.
    """
    target = root / relative_path
    if not target.is_file():
        raise ProtocolLockError(f"cannot hash missing artifact: {relative_path}")
    return sha256_text_file(target) if mode == "text" else sha256_file(target)


def build_lock(root: Path, protocol_version: str, frozen_at: str) -> ProtocolLock:
    """Compute digests for every required artifact under ``root``.

    Raises:
        ProtocolLockError: If a required artifact is missing.
    """
    missing: list[str] = []
    entries: list[LockEntry] = []
    for relative_path, mode, optional in LOCKED_ARTIFACTS:
        if not (root / relative_path).is_file():
            if not optional:
                missing.append(relative_path)
            continue
        entries.append(
            LockEntry(
                path=relative_path,
                sha256=compute_digest(root, relative_path, mode),
                mode=mode,
            )
        )
    if missing:
        raise ProtocolLockError(
            "cannot freeze the protocol; missing required artifacts: " + ", ".join(sorted(missing))
        )
    return ProtocolLock(
        protocol_version=protocol_version,
        frozen_at=frozen_at,
        entries=tuple(entries),
    )


def load_lock(path: Path) -> ProtocolLock:
    """Read a lock file written by :meth:`ProtocolLock.write`.

    Raises:
        ProtocolLockError: If the file is missing or malformed.
    """
    if not path.is_file():
        raise ProtocolLockError(f"no protocol lock at {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise ProtocolLockError(f"protocol lock is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolLockError("protocol lock must be a JSON object")

    raw_entries = payload.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ProtocolLockError("protocol lock has no entries")

    entries: list[LockEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict) or not {"path", "sha256", "mode"} <= raw.keys():
            raise ProtocolLockError(f"malformed lock entry: {raw!r}")
        if raw["mode"] not in {"text", "bytes"}:
            raise ProtocolLockError(f"unknown digest mode: {raw['mode']!r}")
        entries.append(
            LockEntry(
                path=str(raw["path"]),
                sha256=str(raw["sha256"]),
                mode=cast("DigestMode", raw["mode"]),
            )
        )
    return ProtocolLock(
        protocol_version=str(payload.get("protocol_version", "")),
        frozen_at=str(payload.get("frozen_at", "")),
        entries=tuple(entries),
    )


def verify_lock(root: Path, lock: ProtocolLock) -> list[str]:
    """Return a list of human-readable violations; empty means the lock holds."""
    violations: list[str] = []
    for entry in lock.entries:
        target = root / entry.path
        if not target.is_file():
            violations.append(f"{entry.path}: frozen artifact is missing")
            continue
        actual = sha256_text_file(target) if entry.mode == "text" else sha256_file(target)
        if actual != entry.sha256:
            violations.append(
                f"{entry.path}: digest changed after freeze "
                f"(expected {entry.sha256[:12]}…, got {actual[:12]}…)"
            )
    return violations


def assert_lock_valid(root: Path, lock_path: Path) -> ProtocolLock:
    """Load and verify the lock, raising on any violation.

    Raises:
        ProtocolLockError: If the lock is missing, malformed, or violated.
    """
    lock = load_lock(lock_path)
    violations = verify_lock(root, lock)
    if violations:
        raise ProtocolLockError(
            "the frozen protocol has been modified:\n  - " + "\n  - ".join(violations)
        )
    return lock
