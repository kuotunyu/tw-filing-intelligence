"""Recall at a fixed character budget: the cross-parser comparison recall@k cannot make.

D-030: at one ``top_k`` the baseline returns about eight times the text of the candidate, so
recall at a fixed chunk count rewards whichever parser packs more into a chunk. These tests pin
the three decisions that make a budget fair -- whitespace is not charged, overlap is, and the
chunk that crosses the budget still counts.
"""

from __future__ import annotations

from twfi.index.retrieve import Hit, characters_of, recall_at_budget


def hit(index: int, *, pages: tuple[int, ...], text: str, doc: str = "1301-FY2023-AR") -> Hit:
    return Hit(
        chunk_index=index,
        score=1.0 / (index + 1),
        chunk_id=f"{doc}:fixed:{index:05d}",
        doc_id=doc,
        pages=pages,
        text=text,
    )


def test_whitespace_is_not_charged() -> None:
    """The two parsers lay whitespace out differently; charging it compares layout."""
    assert characters_of("存 貨\n287,868,810\n") == characters_of("存貨287,868,810")


def test_a_hit_within_budget_counts() -> None:
    hits = [hit(0, pages=(1,), text="x" * 100), hit(1, pages=(188,), text="y" * 100)]
    assert recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=500)


def test_a_hit_beyond_the_budget_does_not() -> None:
    hits = [hit(0, pages=(1,), text="x" * 600), hit(1, pages=(188,), text="y" * 100)]
    assert not recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=500)


def test_the_chunk_that_crosses_the_budget_still_counts() -> None:
    """Otherwise the metric depends on chunk size again, in the opposite direction: a parser
    with large chunks would have its last and possibly decisive hit discarded more often."""
    hits = [hit(0, pages=(1,), text="x" * 400), hit(1, pages=(188,), text="y" * 900)]
    assert recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=500)


def test_overlapping_text_is_charged_twice() -> None:
    """The baseline's windows overlap by 100 of every 800 characters and pay for it.

    Not a penalty: a budget spent re-delivering text the reader already has is spent, and
    deduplicating would credit the baseline for redundancy the answering model still reads.
    """
    same = "x" * 300
    hits = [
        hit(0, pages=(1,), text=same),
        hit(1, pages=(2,), text=same),
        hit(2, pages=(188,), text="y"),
    ]
    assert not recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=600)


def test_a_record_naming_no_page_is_not_a_success() -> None:
    hits = [hit(0, pages=(188,), text="y" * 10)]
    assert not recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[], budget=10_000)


def test_a_non_positive_budget_buys_nothing() -> None:
    hits = [hit(0, pages=(188,), text="y" * 10)]
    assert not recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=0)


def test_a_hit_in_another_document_does_not_count() -> None:
    hits = [hit(0, pages=(188,), text="y" * 10, doc="2330-FY2023-AR")]
    assert not recall_at_budget(hits, doc_id="1301-FY2023-AR", pages=[188], budget=10_000)


def test_no_hits_at_all_is_a_miss_rather_than_an_error() -> None:
    assert not recall_at_budget([], doc_id="1301-FY2023-AR", pages=[188], budget=10_000)
