"""Protocol 3.2's four retrieval metrics, which the study gates on.

These were missing. `eval_retrieval.py` measured page-level recall@10 and @20 -- adjacent to the
protocol's Recall@5 and MRR@10 but not the same quantity, and `fetch_depth` had been chosen
against the non-registered one. Nothing here is frozen yet, so the fix is to implement what the
protocol says and re-choose on that.

The distinction the tests pin hardest is between Recall@5 (did *any* required evidence arrive)
and complete-evidence coverage (did *all* of it), because a question whose evidence spans two
pages is answerable only under the second and Recall@5 cannot see the difference.
"""

from __future__ import annotations

from twfi.eval.gold import EvidenceRef
from twfi.index.retrieve import Hit, covered_targets, hit_rank, reciprocal_rank

DOC = "1301-FY2023-AR"
OTHER = "2412-FY2023-AR"


def hit(index: int, doc: str, pages: tuple[int, ...]) -> Hit:
    return Hit(
        chunk_index=index,
        score=1.0 / (index + 1),
        chunk_id=f"{doc}:struct:{index:05d}",
        doc_id=doc,
        pages=pages,
        text="x" * 50,
    )


# ------------------------------------------------------- parsing an evidence reference


def test_a_table_cell_reference_yields_its_document_and_page() -> None:
    ref = EvidenceRef(kind="table_cell", ref="1301-FY2023-AR#p188/資產總計/112年度")
    assert ref.location == ("1301-FY2023-AR", 188)


def test_a_bare_page_reference_parses() -> None:
    assert EvidenceRef(kind="page", ref="1301-FY2023-AR#p191").location == ("1301-FY2023-AR", 191)


def test_a_chart_crop_reference_parses() -> None:
    ref = EvidenceRef(kind="chart_crop", ref="2330-FY2023-AR#p7/產能計劃/年成長率")
    assert ref.location == ("2330-FY2023-AR", 7)


def test_a_reference_naming_no_page_yields_none() -> None:
    """A structured row points into DuckDB, not at a page, so retrieval cannot be scored on it."""
    assert EvidenceRef(kind="sql_row", ref="fin_line_item/1301/FY2023/資產總計").location is None


# ------------------------------------------------------------------ Recall@5 and MRR@10


def test_the_rank_is_one_based_so_a_first_place_hit_scores_one() -> None:
    hits = [hit(0, DOC, (188,))]
    assert hit_rank(hits, {(DOC, 188)}) == 1
    assert reciprocal_rank(hits, {(DOC, 188)}, k=10) == 1.0


def test_a_hit_at_rank_four_scores_a_quarter() -> None:
    hits = [hit(i, DOC, (1,)) for i in range(3)] + [hit(3, DOC, (188,))]
    assert hit_rank(hits, {(DOC, 188)}) == 4
    assert reciprocal_rank(hits, {(DOC, 188)}, k=10) == 0.25


def test_a_hit_beyond_k_scores_zero_rather_than_being_dropped() -> None:
    """Omitting unfound questions from the mean would reward failing completely."""
    hits = [hit(i, DOC, (1,)) for i in range(10)] + [hit(10, DOC, (188,))]
    assert hit_rank(hits, {(DOC, 188)}) == 11
    assert reciprocal_rank(hits, {(DOC, 188)}, k=10) == 0.0


def test_any_one_target_is_enough_for_the_rank() -> None:
    """Recall@5 asks whether *any* required evidence arrived."""
    hits = [hit(0, DOC, (191,))]
    assert hit_rank(hits, {(DOC, 188), (DOC, 191)}) == 1


def test_a_record_with_no_targets_has_no_rank() -> None:
    assert hit_rank([hit(0, DOC, (188,))], set()) is None
    assert reciprocal_rank([hit(0, DOC, (188,))], set(), k=10) == 0.0


def test_the_same_page_in_another_document_does_not_count() -> None:
    assert hit_rank([hit(0, OTHER, (188,))], {(DOC, 188)}) is None


# ----------------------------------------------- complete and cross-page coverage


def test_complete_coverage_needs_every_target_not_merely_one() -> None:
    """The distinction Recall@5 cannot make."""
    targets = {(DOC, 188), (DOC, 191)}
    partial = [hit(0, DOC, (188,))]
    assert hit_rank(partial, targets) == 1, "Recall@5 is satisfied"
    assert covered_targets(partial, targets, k=5) != targets, "but the evidence is incomplete"

    both = [hit(0, DOC, (188,)), hit(1, DOC, (191,))]
    assert covered_targets(both, targets, k=5) == targets


def test_a_chunk_spanning_two_pages_covers_both() -> None:
    targets = {(DOC, 187), (DOC, 188)}
    assert covered_targets([hit(0, DOC, (187, 188))], targets, k=5) == targets


def test_coverage_respects_the_cutoff() -> None:
    targets = {(DOC, 188), (DOC, 191)}
    hits = [hit(0, DOC, (188,))] + [hit(i, DOC, (1,)) for i in range(1, 5)] + [hit(5, DOC, (191,))]
    assert covered_targets(hits, targets, k=5) == {(DOC, 188)}
    assert covered_targets(hits, targets, k=6) == targets


def test_cross_document_evidence_is_scored_against_both_filings() -> None:
    """A metric keyed on one document silently ignores half of a cross_document question."""
    targets = {(DOC, 188), (OTHER, 137)}
    one_side = [hit(0, DOC, (188,))]
    assert covered_targets(one_side, targets, k=5) == {(DOC, 188)}

    both = [hit(0, DOC, (188,)), hit(1, OTHER, (137,))]
    assert covered_targets(both, targets, k=5) == targets


def test_coverage_never_reports_a_target_that_was_not_required() -> None:
    assert covered_targets([hit(0, DOC, (1, 2, 188))], {(DOC, 188)}, k=5) == {(DOC, 188)}
