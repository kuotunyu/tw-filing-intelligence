"""Make script output survive a console that is not UTF-8.

Windows starts a console in the system codepage -- cp950 on this machine -- and Python
encodes stdout with it. A character outside Big5 then aborts the script: listing the audit
sample raised ``UnicodeEncodeError: 'cp950' codec can't encode character '\\u2264'`` on a
gold record whose question contained ``≤``, and the records after it were never printed.
The failure is worse than ugly output, because a listing that stops early looks like a
shorter sample rather than a crashed one.

The same codepage has already cost this project a day once, decoding an editable install's
``.pth`` as cp950 (see docs/DECISIONS.md).

This is not an import side effect. A library that silently rebinds ``sys.stdout`` would
surprise anything importing it, and the offline tests would start depending on it. Scripts
that echo filing text or gold records call ``use_utf8_output()`` themselves.
"""

from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

__all__ = ["use_utf8_output"]


@runtime_checkable
class _Reconfigurable(Protocol):
    """The part of ``TextIOWrapper`` this needs, so a fake stream can stand in."""

    def reconfigure(self, *, encoding: str, errors: str) -> None: ...  # pragma: no cover


def use_utf8_output() -> tuple[str, ...]:
    """Switch stdout and stderr to UTF-8, replacing anything that still cannot encode.

    ``errors="replace"`` is deliberate belt-and-braces: if a stream refuses UTF-8 for a
    reason this cannot foresee, an unprintable character should degrade to a question mark
    rather than lose the rest of the output.

    Returns:
        The names of the streams it changed, so a caller can say so if it wants to. A
        stream that is redirected to a file, or replaced by a test harness, is usually not
        reconfigurable; that is not an error and is reported by omission.
    """
    changed: list[str] = []
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if not isinstance(stream, _Reconfigurable):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):  # pragma: no cover - stream already detached
            continue
        changed.append(name)
    return tuple(changed)
