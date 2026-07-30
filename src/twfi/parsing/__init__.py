"""Document parsing: the baseline parser and the structure-aware candidate.

Exactly two parsers, per ``docs/FEASIBILITY_PROTOCOL.md`` 2.1:

* :mod:`twfi.parsing.baseline` -- PyMuPDF plain text with fixed chunking. F0.
* :mod:`twfi.parsing.layout` -- in-repo layout-aware parsing (font statistics for
  headings, a section tree, reading order). F1 onwards.

No parser leaderboard, no second OCR stack, no cloud parsing API.
"""

from __future__ import annotations

__all__: list[str] = []
