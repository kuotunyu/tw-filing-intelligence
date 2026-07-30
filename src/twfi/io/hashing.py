"""SHA-256 helpers used for provenance and for freezing the protocol.

Two flavours exist on purpose:

* :func:`sha256_bytes` / :func:`sha256_file` — byte-exact, used for downloaded
  artifacts (PDF, XBRL, JSON) where any bit change matters.
* :func:`sha256_text_file` — newline-normalised, used for the text documents that
  make up the frozen protocol so that a CRLF/LF difference between machines does
  not spuriously invalidate the lock.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

__all__ = ["sha256_bytes", "sha256_file", "sha256_text_file", "CHUNK_SIZE"]

CHUNK_SIZE = 1 << 20  # 1 MiB


def sha256_bytes(payload: bytes) -> str:
    """Return the lowercase hex SHA-256 of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the lowercase hex SHA-256 of a file, read in chunks.

    Byte-exact: use this for downloaded binary artifacts.
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text_file(path: Path) -> str:
    """Return the SHA-256 of a text file with newlines normalised to ``\\n``.

    Also strips a UTF-8 BOM and any trailing whitespace-only tail so that editors
    cannot invalidate a frozen protocol by adding or removing a final newline.
    """
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    normalised = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").rstrip() + b"\n"
    return sha256_bytes(normalised)
