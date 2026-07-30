"""Provenance depends on hashing being boring and correct."""

from __future__ import annotations

import hashlib
from pathlib import Path

from twfi.io.hashing import sha256_bytes, sha256_file, sha256_text_file


def test_sha256_bytes_matches_stdlib() -> None:
    payload = "台積電 2330".encode()
    assert sha256_bytes(payload) == hashlib.sha256(payload).hexdigest()


def test_sha256_bytes_of_empty_input() -> None:
    assert sha256_bytes(b"") == hashlib.sha256(b"").hexdigest()


def test_sha256_file_is_byte_exact(tmp_path: Path) -> None:
    target = tmp_path / "blob.bin"
    payload = bytes(range(256)) * 8192  # larger than one chunk
    target.write_bytes(payload)
    assert sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_sha256_file_distinguishes_line_endings(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.txt"
    lf = tmp_path / "lf.txt"
    crlf.write_bytes(b"a\r\nb\r\n")
    lf.write_bytes(b"a\nb\n")
    assert sha256_file(crlf) != sha256_file(lf)


def test_sha256_text_file_normalises_line_endings(tmp_path: Path) -> None:
    crlf = tmp_path / "crlf.md"
    lf = tmp_path / "lf.md"
    cr = tmp_path / "cr.md"
    crlf.write_bytes(b"# t\r\nbody\r\n")
    lf.write_bytes(b"# t\nbody\n")
    cr.write_bytes(b"# t\rbody\r")
    assert sha256_text_file(crlf) == sha256_text_file(lf) == sha256_text_file(cr)


def test_sha256_text_file_ignores_bom_and_trailing_blank_lines(tmp_path: Path) -> None:
    plain = tmp_path / "plain.md"
    decorated = tmp_path / "decorated.md"
    plain.write_bytes(b"content\n")
    decorated.write_bytes(b"\xef\xbb\xbfcontent\n\n\n   \n")
    assert sha256_text_file(plain) == sha256_text_file(decorated)


def test_sha256_text_file_detects_real_edits(tmp_path: Path) -> None:
    before = tmp_path / "before.md"
    after = tmp_path / "after.md"
    before.write_text("threshold >= 10pp\n", encoding="utf-8")
    after.write_text("threshold >= 5pp\n", encoding="utf-8")
    assert sha256_text_file(before) != sha256_text_file(after)
