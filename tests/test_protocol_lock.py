"""Freezing must be tamper-evident.

The last test in this module is the one that matters for research integrity: once
the protocol is frozen, every subsequent ``pytest`` run re-checks the digests.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from twfi.errors import ProtocolLockError
from twfi.eval.protocol_lock import (
    LOCKED_ARTIFACTS,
    LockEntry,
    ProtocolLock,
    assert_lock_valid,
    build_lock,
    compute_digest,
    load_lock,
    verify_lock,
)
from twfi.paths import RepoPaths

FROZEN_AT = "2026-07-31T00:00:00+00:00"


def _make_lockable_repo(root: Path) -> None:
    """Create every artifact that ``LOCKED_ARTIFACTS`` requires."""
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    for relative_path, _mode, _optional in LOCKED_ARTIFACTS:
        target = root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content of {relative_path}\n", encoding="utf-8")


def test_build_lock_covers_every_required_artifact(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock = build_lock(tmp_path, "1.0.0", FROZEN_AT)

    assert lock.protocol_version == "1.0.0"
    assert lock.frozen_at == FROZEN_AT
    assert {entry.path for entry in lock.entries} == {p for p, _, _ in LOCKED_ARTIFACTS}
    assert all(len(entry.sha256) == 64 for entry in lock.entries)


def test_build_lock_refuses_when_artifacts_are_missing(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "FEASIBILITY_PROTOCOL.md").write_text("x\n", encoding="utf-8")
    with pytest.raises(ProtocolLockError, match="missing required artifacts"):
        build_lock(tmp_path, "1.0.0", FROZEN_AT)


def test_compute_digest_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProtocolLockError, match="missing artifact"):
        compute_digest(tmp_path, "docs/nope.md", "text")


def test_compute_digest_bytes_mode(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02")
    assert len(compute_digest(tmp_path, "blob.bin", "bytes")) == 64


def test_roundtrip_write_and_load(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock = build_lock(tmp_path, "1.0.0", FROZEN_AT)
    lock_path = tmp_path / "results" / "feasibility" / "protocol_lock.json"
    lock.write(lock_path)

    reloaded = load_lock(lock_path)
    assert reloaded == lock
    assert verify_lock(tmp_path, reloaded) == []


def test_verify_detects_an_edit_after_freeze(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock = build_lock(tmp_path, "1.0.0", FROZEN_AT)

    # Simulate "the numbers were bad so I lowered the threshold".
    (tmp_path / "docs" / "FEASIBILITY_PROTOCOL.md").write_text("gate >= 5pp\n", encoding="utf-8")

    violations = verify_lock(tmp_path, lock)
    assert len(violations) == 1
    assert "digest changed after freeze" in violations[0]


def test_verify_detects_a_deleted_artifact(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock = build_lock(tmp_path, "1.0.0", FROZEN_AT)
    (tmp_path / "data" / "evaluation" / "locked" / "gold.jsonl").unlink()

    violations = verify_lock(tmp_path, lock)
    assert any("is missing" in v for v in violations)


def test_text_mode_tolerates_line_ending_churn(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock = build_lock(tmp_path, "1.0.0", FROZEN_AT)

    protocol = tmp_path / "docs" / "FEASIBILITY_PROTOCOL.md"
    protocol.write_bytes(protocol.read_bytes().replace(b"\n", b"\r\n"))

    assert verify_lock(tmp_path, lock) == []


def test_assert_lock_valid_raises_on_violation(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock_path = tmp_path / "lock.json"
    build_lock(tmp_path, "1.0.0", FROZEN_AT).write(lock_path)
    (tmp_path / "configs" / "models.lock.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ProtocolLockError, match="has been modified"):
        assert_lock_valid(tmp_path, lock_path)


def test_assert_lock_valid_passes_when_untouched(tmp_path: Path) -> None:
    _make_lockable_repo(tmp_path)
    lock_path = tmp_path / "lock.json"
    build_lock(tmp_path, "1.0.0", FROZEN_AT).write(lock_path)
    assert assert_lock_valid(tmp_path, lock_path).protocol_version == "1.0.0"


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '{"entries": []}',
        '{"entries": [{"path": "a"}]}',
        '{"entries": [{"path": "a", "sha256": "b", "mode": "rot13"}]}',
    ],
)
def test_load_lock_rejects_malformed_payloads(tmp_path: Path, payload: str) -> None:
    lock_path = tmp_path / "lock.json"
    lock_path.write_text(payload, encoding="utf-8")
    with pytest.raises(ProtocolLockError):
        load_lock(lock_path)


def test_load_lock_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ProtocolLockError, match="no protocol lock"):
        load_lock(tmp_path / "absent.json")


def test_lock_entry_json_shape() -> None:
    entry = LockEntry(path="a.md", sha256="0" * 64, mode="text")
    assert entry.to_json() == {"path": "a.md", "sha256": "0" * 64, "mode": "text"}


def test_lock_json_is_stable_and_sorted(tmp_path: Path) -> None:
    lock = ProtocolLock(
        protocol_version="1.0.0",
        frozen_at=FROZEN_AT,
        entries=(LockEntry("b.md", "1" * 64, "text"), LockEntry("a.md", "2" * 64, "text")),
    )
    lock_path = tmp_path / "lock.json"
    lock.write(lock_path)
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    assert list(payload) == ["entries", "frozen_at", "protocol_version"]
    # Entry order is preserved (it mirrors LOCKED_ARTIFACTS), only keys are sorted.
    assert [e["path"] for e in payload["entries"]] == ["b.md", "a.md"]


# --------------------------------------------------------------------------- #
# The integrity check that runs against the real repository.
# --------------------------------------------------------------------------- #


def test_real_protocol_lock_still_holds(repo_root: Path) -> None:
    """Once frozen, the protocol, locked gold set, manifests and model pins are fixed."""
    lock_path = RepoPaths(root=repo_root).protocol_lock_json
    if not lock_path.is_file():
        pytest.skip("protocol not frozen yet (run scripts/freeze_protocol.py)")
    assert_lock_valid(repo_root, lock_path)
