"""Locate evidence a person still has to read, and never state what it says.

Finding the 合併綜合損益表 in a 345-page annual report is the tedious part of
annotation; deciding what the figure on it means is the part that must stay human.
This module does only the first, and the type it returns --
:class:`twfi.eval.gold.DraftItem` -- has no field an answer could be written into.

Probes (gate G8) are not unanswerable questions, and the difference decides what a
good probe is. An ``unanswerable`` question asks for something the filings do not
contain, with retrieval running normally. A probe asks something the filings *do*
contain, and then the harness withholds the evidence: retrieval is forced empty and the
system must refuse rather than answer from what it memorised in pretraining.

So a probe is only a real test if the model is likely to know the answer anyway. A
probe about an obscure figure proves nothing -- the model would have failed it for the
wrong reason. The topics below are therefore the headline figures of well-known issuers,
chosen to be as tempting as possible.

For the same reason a probe record keeps its true answer. Error analysis then separates
three outcomes that matter differently: refused correctly, answered wrongly, and -- the
worst case, invisible if the answer were absent -- answered *correctly* with no evidence
at all.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from twfi.eval.gold import CompanyRef, DraftItem, EvidenceRef

__all__ = [
    "AnchorHit",
    "ProbeTopic",
    "PROBE_TOPICS",
    "STATEMENT_CONTEXT",
    "EXCERPT_CHARS",
    "page_hits",
    "probe_slots",
]

#: How much of the page to carry into the worklist. Enough to recognise the page when
#: it is opened, short enough that nobody mistakes the worklist for the source.
EXCERPT_CHARS: Final = 120

#: A page mentioning 營業收入 in prose is not a statement page. Requiring one of these
#: as well is what separates the 合併損益表 from the twenty pages that discuss it.
STATEMENT_CONTEXT: Final[tuple[str, ...]] = (
    "合併綜合損益表",
    "合併損益表",
    "綜合損益表",
    "資產負債表",
    "合併資產負債表",
)

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class AnchorHit:
    """One page that mentions a term, with enough text to recognise it by."""

    page: int
    term: str
    excerpt: str
    context_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProbeTopic:
    """A headline figure a pretrained model plausibly memorised.

    ``terms`` are the strings that locate the figure on a statement page. They locate
    it; they do not read it.
    """

    key: str
    label: str
    terms: tuple[str, ...]
    unit: str | None = None
    currency: str | None = None

    def question_stem(self, company: str, period: str) -> str:
        """A neutral stem for the annotator to accept, sharpen, or discard."""
        return f"{company} {period} 的{self.label}是多少？"


#: Deliberately the most famous figures in the set. See the module docstring: a probe
#: the model could not have memorised would pass for the wrong reason.
PROBE_TOPICS: Final[tuple[ProbeTopic, ...]] = (
    ProbeTopic("revenue", "營業收入", ("營業收入",), unit="千元", currency="TWD"),
    ProbeTopic("gross_profit", "營業毛利", ("營業毛利",), unit="千元", currency="TWD"),
    ProbeTopic("operating_income", "營業利益", ("營業利益",), unit="千元", currency="TWD"),
    ProbeTopic("net_income", "本期淨利", ("本期淨利", "淨利（淨損）"), unit="千元", currency="TWD"),
    ProbeTopic("eps", "基本每股盈餘", ("基本每股盈餘", "每股盈餘"), unit="元", currency="TWD"),
    ProbeTopic("total_assets", "資產總額", ("資產總額", "資產總計"), unit="千元", currency="TWD"),
)


def _normalise(text: str) -> str:
    return _WHITESPACE.sub("", text)


def page_hits(
    pages: Sequence[str],
    terms: Sequence[str],
    *,
    context: Sequence[str] = STATEMENT_CONTEXT,
    require_context: bool = True,
) -> list[AnchorHit]:
    """Pages where any term appears, optionally only on pages that look like statements.

    ``pages`` is 0-indexed; the returned ``page`` is 1-based, matching every page
    number in the gold schema and every PDF viewer a person will open.
    """
    hits: list[AnchorHit] = []
    for index, raw in enumerate(pages):
        flat = _normalise(raw)
        if not flat:
            continue
        present = tuple(term for term in context if term in flat)
        if require_context and not present:
            continue
        for term in terms:
            position = flat.find(term)
            if position < 0:
                continue
            start = max(0, position - EXCERPT_CHARS // 3)
            hits.append(
                AnchorHit(
                    page=index + 1,
                    term=term,
                    excerpt=flat[start : start + EXCERPT_CHARS],
                    context_terms=present,
                )
            )
            break
    return hits


def statement_pages(
    pages: Sequence[str], *, context: Sequence[str] = STATEMENT_CONTEXT
) -> dict[str, list[int]]:
    """Where each statement heading appears, as orientation for the annotator."""
    found: dict[str, list[int]] = {}
    for index, raw in enumerate(pages):
        flat = _normalise(raw)
        for term in context:
            if term in flat:
                found.setdefault(term, []).append(index + 1)
    return found


def probe_slots(
    *,
    doc_id: str,
    company: CompanyRef,
    period: str,
    pages: Sequence[str],
    source_url: str | None = None,
    topics: Sequence[ProbeTopic] = PROBE_TOPICS,
    max_pages_per_topic: int = 2,
) -> list[DraftItem]:
    """One draft slot per topic that was actually located in this document.

    Topic terms are located *without* requiring a statement heading on the same page.
    Requiring both was wrong on real filings: in 2330's FY2024 financial report the
    heading appears on pages 2/18/42 while 營業收入 appears on 55/56/76/82, because a
    statement's heading sits on its first page and the figures continue past it -- and
    in that document the headline figures are only machine-readable in the notes at all.
    The conjunction found nothing in the one document that matters most.

    Heading locations are still reported, as orientation rather than as a filter.
    """
    headings = statement_pages(pages)
    slots: list[DraftItem] = []
    for topic in topics:
        hits = page_hits(pages, topic.terms, require_context=False)
        if not hits:
            continue
        chosen = hits[:max_pages_per_topic]
        page_numbers = tuple(hit.page for hit in chosen)
        slots.append(
            DraftItem(
                draft_id=f"PROBE-{doc_id}-{topic.key}",
                question_type="narrative_fact",
                company=company,
                period=period,
                source_document=(doc_id,),
                evidence_hint=tuple(EvidenceRef("page", f"{doc_id}#p{hit.page}") for hit in chosen),
                page_numbers=page_numbers,
                unit=topic.unit,
                currency=topic.currency,  # type: ignore[arg-type]
                source_url=(source_url,) if source_url else (),
                rationale=(
                    f"{topic.label} located on page(s) {list(page_numbers)} via "
                    f"{[hit.term for hit in chosen]}. Statement headings in this "
                    f"document: { {term: pgs[:4] for term, pgs in headings.items()} }"
                ),
                notes_for_annotator=(
                    f"Suggested stem: {topic.question_stem(company.name, period)}\n"
                    f"Open the page, read the figure, and write both the question and the "
                    f"answer yourself. Excerpt (for recognising the page only, not a source):\n"
                    + "\n".join(f"  p{hit.page}: {hit.excerpt}" for hit in chosen)
                ),
            )
        )
    return slots
