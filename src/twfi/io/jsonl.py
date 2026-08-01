"""Write and read JSONL that survives ``str.splitlines()``.

JSONL means one record per ``\\n``. The trap is that Python disagrees with JSON about what a
line break is: ``json.dumps(..., ensure_ascii=False)`` leaves U+2028 LINE SEPARATOR, U+2029
PARAGRAPH SEPARATOR and U+0085 NEXT LINE unescaped inside strings, while ``str.splitlines()``
treats all three as line boundaries. A record containing one is written as a single line and
read back as two fragments, the first of which is an unterminated string.

This was not hypothetical. ``data/index/candidate/chunks.jsonl`` held a chunk of filing prose
carrying one of them, and loading it failed with
``JSONDecodeError: Unterminated string starting at: line 1 column 360``. Filing text is exactly
where such characters turn up: it is typeset, converted, and passed through several tools before
reaching us.

Escaping at the writer rather than splitting differently at each reader, because the readers are
many and easy to add -- ``load_gold`` splits with ``splitlines()`` too -- and one of them will
eventually be written by someone who has never heard of U+2028.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

__all__ = ["LINE_BREAKS", "dumps_line", "dump_lines", "iter_lines", "read_lines"]

#: Characters JSON leaves alone and ``str.splitlines()`` breaks on. Escaped so that a JSONL
#: file has exactly as many lines as it has records, whichever splitter reads it.
LINE_BREAKS: dict[str, str] = {
    " ": "\\u2028",
    " ": "\\u2029",
    "": "\\u0085",
}


def dumps_line(payload: Mapping[str, Any]) -> str:
    """One JSONL record: compact, UTF-8 readable, and free of stray line breaks.

    ``ensure_ascii=False`` is kept deliberately -- these files are full of Chinese and escaping
    it all would make them unreadable to the people who have to check them by eye.
    """
    text = json.dumps(payload, ensure_ascii=False)
    for character, escape in LINE_BREAKS.items():
        text = text.replace(character, escape)
    return text


def dump_lines(path: Path, payloads: Iterable[Mapping[str, Any]]) -> int:
    """Write records to a JSONL file, returning how many. Creates parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [dumps_line(payload) for payload in payloads]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def iter_lines(text: str) -> Iterator[dict[str, Any]]:
    """Parse JSONL text, skipping blanks and ``//`` comments.

    Splits on ``\\n`` alone rather than with ``splitlines()``. Both work on a file written by
    :func:`dump_lines`, but a file written by something else may still contain U+2028, and this
    reader should not be the one that fails on it.
    """
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        yield json.loads(stripped)


def read_lines(path: Path) -> list[dict[str, Any]]:
    """Every record in a JSONL file."""
    return list(iter_lines(path.read_text(encoding="utf-8")))
