"""Bring extracted text to one canonical form, at the point it leaves the PDF.

Some of these filings encode characters that *render* correctly and *compare* wrongly.
``1301-FY2023-AR`` sets 年 as U+F98E and 度 as U+FA01 -- CJK Compatibility Ideographs,
visually identical to U+5E74 and U+5EA6 and not equal to them. So the string 「年度」 does not
appear anywhere on 91 of that filing's 358 CJK-bearing pages, even though a reader sees it on
every one of them.

Nothing downstream can recover from that alone. BM25 would miss those pages, dense retrieval
would embed a rare-codepoint token, the unit-declaration regexes would not fire, and
``verify_gold_answers`` would call a correct gold answer absent. Every one of those looks like
a retrieval or annotation failure and none of them is.

**NFC, not NFKC.** The compatibility ideographs have *canonical* decompositions, so NFC is
enough to fix them, and NFC leaves everything else alone. NFKC also rewrites fullwidth forms
-- ：→ :, （→ (, ，→ , -- and this codebase matches Chinese punctuation literally in several
places (``tables.py``'s unit-exception character class is ``[^，。、）)]``), so NFKC would have
silently changed the behaviour of regexes far from here. Measured on the corpus: NFC recovers
the same 91 pages NFKC does. The extra breadth bought nothing and risked a lot.

Applied to **both** parsers, baseline and layout-aware, and to the table extractor. That is
the point: the study compares F0 against F7, so a fix that reached only the candidate would
show up as the candidate's gain. Compatibility-character breakage is a property of the corpus,
not a factor under test, so it comes out of both sides or neither.

What this does not fix is the other failure mode here. ``2412-FY2023-AR`` extracts 64,238
characters spread across Cyrillic, Armenian, Syriac, Ethiopic and Tibetan -- raw glyph indices
from a subset font with no ToUnicode map. Those codepoints bear no relation to the characters
on the page, so normalisation recovers exactly zero pages of it (measured). That one is a
corpus limitation to report, not a bug to fix here.
"""

from __future__ import annotations

import unicodedata

__all__ = ["normalise"]


def normalise(text: str) -> str:
    """Canonicalise extracted text so equal-looking characters compare equal.

    Idempotent, and a no-op for text that is already canonical -- which is nearly all of it,
    so this is not worth trying to make conditional.
    """
    if not text:
        return text
    return unicodedata.normalize("NFC", text)
