"""Data acquisition and integrity primitives.

Every outbound HTTP request in this project goes through :mod:`twfi.io.http`,
which enforces a hard-coded host allowlist. Nothing here ever takes a URL from
document content or model output.
"""

from __future__ import annotations

__all__: list[str] = []
