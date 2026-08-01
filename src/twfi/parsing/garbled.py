"""Detect pages whose text layer decoded to the wrong characters.

A filing can be 100% "readable" by the page-has-text measure and still be unusable: the extractor
returns characters, they are simply the wrong ones. `2317-FY2023-AR`'s title extracts as
``Ψҗᇙ୍ܺ``. The document-quality metric cannot see that, and neither could the first attempt at
this module, which is why this is the second.

**Why the first attempt was withdrawn.** It blacklisted codepoint ranges it judged suspicious.
That is guessing, and the guess had a hole at U+2C00-2E7F -- where the character in its own
example lived -- so it scored `1301-FY2023-AR` at 0% broken. A blacklist has to be right about
every range that could appear; an allow-list only has to be right about the ones that do.

**So the allow-list was written from a census of the corpus rather than from judgement.** Over
2,161,561 non-whitespace characters in the eight usable filings, the ranges below account for
more than 99.5% of every document that is not corrupt. What falls outside them, per document:

    2412-FY2023-AR   17.91%   indic, cyrillic, thai, hebrew, greek -- scattered across scripts
    1301-FY2023-AR   15.45%   latin-ext, latin-1, and 3.6% C0 control characters
    2882-FY2024-AR    0.29%   almost all private-use, plausibly a logo font
    2330-FY2024-AR    0.10%
    2330-FY2023-AR    0.05%
    2317-FY2024-FS    0.00%
    2330-FY2024-FS    0.00%
    2882-FY2024-FS    0.00%

Two documents at 15% to 18% and six at or under a third of a percent: a fiftyfold gap, so the
threshold is not a tuned parameter. Per page the separation holds -- the six sound documents put
their worst page at 0.23% to 2.73% (one page of `2882-FY2024-AR` reaches 15%), while the two
corrupt ones sit at about 51% at the ninetieth percentile and flag 43% to 48% of their pages.
:data:`GARBLED_THRESHOLD` is 5%, between those.

The two corrupt documents fail *differently*, and the mode is reported rather than collapsed:
one is scattered across unrelated scripts (a CMap that maps glyphs to the wrong codepoints), the
other is Latin-1 range plus control characters (a byte stream decoded as the wrong encoding).
They call for different remedies, so "garbled" alone would throw away the useful half.

**These two documents are the development set.** Every tuning decision in the study rests on the
two filings with broken text layers, which is a fact about what the numbers on dev can mean, not
a defect in this module. Recorded in D-024 and D-033.

Pure and offline: no model, no PDF, no I/O. Takes text and returns counts.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "ALLOWED_CHARACTERS",
    "GARBLED_THRESHOLD",
    "MIN_CHARACTERS",
    "PageDefects",
    "DocumentDefects",
    "is_core",
    "page_defects",
    "document_defects",
]

#: Codepoint ranges that make up a Traditional Chinese filing. Each is here because the census
#: found it, not because it seemed reasonable -- see the module docstring.
CORE_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x20, 0x7E, "printable ASCII: figures, codes, English company and product names"),
    (0x2000, 0x206F, "general punctuation: the dashes and quotation marks filings actually use"),
    (0x2070, 0x20CF, "superscripts, subscripts and currency signs"),
    (0x2100, 0x21FF, "letterlike symbols, number forms and arrows: ℃, №, →"),
    (0x2200, 0x22FF, "mathematical operators: ±, ×, ÷, ≒, ≦"),
    (0x2460, 0x24FF, "enclosed alphanumerics: ①②③ number note items"),
    (0x25A0, 0x27BF, "geometric shapes and dingbats: ○●△▲□■ mark table rows"),
    (0x3000, 0x303F, "CJK symbols and punctuation, including the ideographic space"),
    (0x3100, 0x312F, "bopomofo: rare, and legitimate when a filing glosses a reading"),
    (0x3400, 0x4DBF, "CJK extension A"),
    (0x4E00, 0x9FFF, "CJK unified ideographs: half the corpus"),
    (0xF900, 0xFAFF, "CJK compatibility ideographs -- NFC removes most, not all (D-024)"),
    (0xFF00, 0xFFEF, "halfwidth and fullwidth forms"),
)

#: Above this share of off-core characters, a page's text layer is not usable. Measured, not
#: chosen: see the module docstring for the per-page distributions it sits between.
GARBLED_THRESHOLD = 0.05

#: A page with fewer characters than this is not judged. A cover page holding six characters can
#: cross any rate threshold on one stray glyph, and calling that a broken text layer would put
#: noise into a measurement whose whole purpose is to be trusted.
MIN_CHARACTERS = 50


#: Individual characters outside the core ranges that sound filings do use -- and *individually*,
#: never by range. The six sound documents use exactly fourteen characters from Latin-1, Latin
#: Extended and Greek, 272 occurrences in total: ® é × § ö ó ° ÷ í Ö ± Δ ä Ø. Registered marks,
#: European names in company and product names, and a few maths signs. The two corrupt documents
#: use 574 distinct characters from those same blocks, hundreds of occurrences each -- ± alone
#: appears 707 times as mojibake against twice legitimately.
#:
#: **Allowing the blocks rather than the characters was measured and rejected.** Permitting
#: U+00C0-U+024F wholesale takes `1301-FY2023-AR` from 17.32% off-core to 8.09%, because its
#: corruption is concentrated in exactly that range (Ǵ Ƕ Ȑ ȑ ...). At the document level that
#: still clears the threshold; per page it would drop many pages under it, which is the
#: false-negative the withdrawn first version produced. This curated set instead leaves the
#: corrupt documents at 15.45% and 17.91% while taking the sound ones to 0.00%-0.29%.
#:
#: The remaining risk is the cheap one. A filing legitimately using ñ or € that is not listed here
#: raises its page's rate by a character or two, which cannot cross a 5% threshold on its own, and
#: a flagged page gets looked at. A missing *range* hides a broken page instead.
ALLOWED_CHARACTERS = frozenset(
    "®é×§öó°÷íÖ±Δäø"  # every one observed in the sound documents
    "¥€µ‰Ω©²³½"  # currency, micron, per-mille, ohm, superscripts: filings use these
    "àáâãçèêëìîïñòôõùúûüýÿÁÉÍÓÚÜÑÇ"  # precomposed European letters, for names
)


def is_core(character: str) -> bool:
    """Whether a character is one a sound filing is made of."""
    if character in ALLOWED_CHARACTERS:
        return True
    code = ord(character)
    return any(low <= code <= high for low, high, _ in CORE_RANGES)


@dataclass(frozen=True, slots=True)
class PageDefects:
    """What one page's characters are, and whether they can be read."""

    page: int
    characters: int
    #: Off-core characters that are letters of some other script: the CMap failure mode.
    off_script: int
    #: C0 and C1 controls other than tab, newline and carriage return.
    controls: int
    #: Private use area. Unreadable by definition -- no character is assigned there.
    private_use: int
    #: Everything else outside the core ranges.
    other: int

    @property
    def defects(self) -> int:
        return self.off_script + self.controls + self.private_use + self.other

    @property
    def rate(self) -> float:
        """Share of non-whitespace characters outside the core ranges."""
        return self.defects / self.characters if self.characters else 0.0

    @property
    def judged(self) -> bool:
        return self.characters >= MIN_CHARACTERS

    @property
    def garbled(self) -> bool:
        return self.judged and self.rate > GARBLED_THRESHOLD

    @property
    def mode(self) -> str:
        """Which failure this page looks like, or ``""`` when it is not garbled.

        Reported because the two corrupt documents in this corpus fail differently and the
        remedies differ: a wrong CMap needs the glyph mapping, a wrong encoding needs the bytes.
        """
        if not self.garbled:
            return ""
        largest = max(
            (self.off_script, "off_script"),
            (self.controls, "control_characters"),
            (self.private_use, "private_use"),
            (self.other, "other"),
        )
        return largest[1]


@dataclass(frozen=True, slots=True)
class DocumentDefects:
    """One document's pages, and the summary a report can print."""

    doc_id: str
    pages: tuple[PageDefects, ...]

    @property
    def judged_pages(self) -> tuple[PageDefects, ...]:
        return tuple(page for page in self.pages if page.judged)

    @property
    def garbled_pages(self) -> tuple[PageDefects, ...]:
        return tuple(page for page in self.pages if page.garbled)

    @property
    def garbled_share(self) -> float:
        """Share of *judged* pages that are garbled.

        Judged rather than all: including pages too short to assess would divide by a number
        that has nothing to do with what was measured, and the resulting figure would drift
        with how many blank pages a filing happens to have.
        """
        judged = self.judged_pages
        return len(self.garbled_pages) / len(judged) if judged else 0.0

    @property
    def rate(self) -> float:
        """Off-core share over the whole document."""
        characters = sum(page.characters for page in self.pages)
        return sum(page.defects for page in self.pages) / characters if characters else 0.0

    @property
    def modes(self) -> tuple[str, ...]:
        """Every failure mode seen among the garbled pages, commonest first."""
        counts: dict[str, int] = {}
        for page in self.garbled_pages:
            counts[page.mode] = counts.get(page.mode, 0) + 1
        return tuple(mode for mode, _ in sorted(counts.items(), key=lambda item: -item[1]))


def page_defects(page: int, text: str) -> PageDefects:
    """Classify one page's characters. Whitespace is ignored, not counted as either."""
    off_script = controls = private_use = other = total = 0
    for character in text:
        if character.isspace():
            continue
        total += 1
        if is_core(character):
            continue
        code = ord(character)
        if code < 0x20 or 0x7F <= code <= 0x9F:
            controls += 1
        elif 0xE000 <= code <= 0xF8FF or 0xF0000 <= code <= 0x10FFFD:
            private_use += 1
        elif unicodedata.category(character).startswith(("L", "M")):
            # A letter or a combining mark from some other script. Cyrillic where Chinese
            # belongs is the signature of a font whose CMap maps glyphs to the wrong codepoints.
            off_script += 1
        else:
            other += 1
    return PageDefects(
        page=page,
        characters=total,
        off_script=off_script,
        controls=controls,
        private_use=private_use,
        other=other,
    )


def document_defects(doc_id: str, pages: Iterable[tuple[int, str]]) -> DocumentDefects:
    """Classify a document, given ``(page number, text)`` pairs."""
    return DocumentDefects(
        doc_id=doc_id,
        pages=tuple(page_defects(number, text) for number, text in pages),
    )
