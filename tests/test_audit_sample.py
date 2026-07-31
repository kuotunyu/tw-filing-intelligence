"""The audit sample has to be beyond the drafter's reach.

Every test here is about that one property. The sample is the only check on model-chosen
questions, so a drafter who could influence which records get looked at would have
defeated it while leaving the paperwork intact.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from twfi.eval.audit import (
    ALWAYS_AUDITED,
    AUDIT_SEED,
    DEFAULT_SAMPLE,
    audit_sample,
    eligible_records,
)
from twfi.eval.gold import BBoxRef, CompanyRef, EvidenceRef, GoldRecord, Tolerance

TODAY = dt.date(2026, 8, 1)


def narrative(number: int, **overrides: Any) -> GoldRecord:
    """A model-drafted narrative record, numbered so ids sort predictably."""
    base: dict[str, Any] = {
        "question_id": f"LOCK-{number:04d}",
        "question_type": "narrative_fact",
        "question": "年報如何說明先進封裝產能規劃？",
        "answer": "說明將擴充 CoWoS 產能。",
        "company": CompanyRef("台積電", "2330"),
        "period": "FY2023",
        "source_document": ("2330-FY2023-AR",),
        "required_evidence": (EvidenceRef("page", "2330-FY2023-AR#p120"),),
        "answer_provenance": "human_read_pdf",
        "annotated_at": TODAY,
        "page_numbers": (120,),
        "question_author": "claude-opus-5",
    }
    base.update(overrides)
    return GoldRecord(**base)


def chart(number: int, **overrides: Any) -> GoldRecord:
    """A model-drafted chart record, the type that is never sampled."""
    return narrative(
        number,
        question_type="chart_value_trend",
        question="「產能計劃」圖中的年成長率，民國111年、112年、113年分別是多少？",
        answer="民國111年 9%；民國112年 6%；民國113年 6%",
        required_evidence=(EvidenceRef("chart_crop", "2330-FY2023-AR#p7/產能計劃/年成長率"),),
        page_numbers=(7,),
        bbox=(BBoxRef(page=7, bbox=(68.0, 68.0, 290.0, 210.0)),),
        tolerance=Tolerance("absolute", 0.1),
        unit="%",
        annotator="claude-opus-5",
        answer_provenance="model_read_rendered_page",
        **overrides,
    )


def test_a_record_a_person_wrote_and_answered_is_not_in_the_pool() -> None:
    """The audit checks machine work; human work has nothing for it to check."""
    human = narrative(1, question_author="human")
    assert eligible_records([human]) == []


def test_a_human_answer_under_a_machine_question_is_still_eligible() -> None:
    """Question selection is what the audit is for, so authorship of the answer is not enough."""
    assert [r.question_id for r in eligible_records([narrative(1)])] == ["LOCK-0001"]


def test_the_same_records_yield_the_same_sample() -> None:
    records = [narrative(n) for n in range(1, 21)]
    assert audit_sample(records, size=5) == audit_sample(records, size=5)


def test_reordering_the_file_cannot_change_the_sample() -> None:
    """A drafter controls the order lines are written in, and must gain nothing by it."""
    records = [narrative(n) for n in range(1, 21)]
    forward = [r.question_id for r in audit_sample(records, size=5)]
    backward = [r.question_id for r in audit_sample(list(reversed(records)), size=5)]
    assert forward == backward


def test_every_chart_record_is_audited_however_small_the_sample() -> None:
    """size=1 must still not leave a chart question unchecked."""
    records = [narrative(n) for n in range(1, 21)] + [chart(30), chart(31)]
    sampled = {r.question_id for r in audit_sample(records, size=1)}
    assert {"LOCK-0030", "LOCK-0031"} <= sampled


def test_adding_a_chart_record_does_not_redraw_the_other_types() -> None:
    """Otherwise introducing a forced type would silently invalidate finished audits.

    This is the reason forced records are excluded from the draw rather than appended to
    it: a person who has already checked eight records should not have to check eight
    different ones because a new category appeared.
    """
    before = [narrative(n) for n in range(1, 21)]
    after = [*before, chart(30)]
    drawn_before = [r.question_id for r in audit_sample(before, size=5)]
    drawn_after = [
        r.question_id for r in audit_sample(after, size=5) if r.question_type != "chart_value_trend"
    ]
    assert drawn_before == drawn_after


def test_a_small_pool_is_audited_whole_rather_than_sampled() -> None:
    records = [narrative(n) for n in range(1, 4)]
    assert len(audit_sample(records, size=DEFAULT_SAMPLE)) == 3


def test_the_sample_comes_back_in_id_order() -> None:
    """Forced records are merged into the order, not appended after it."""
    records = [narrative(n) for n in range(1, 21)] + [chart(2)]
    ids = [r.question_id for r in audit_sample(records, size=5)]
    assert ids == sorted(ids)


def test_the_seed_is_the_protocol_seed() -> None:
    """A seed chosen by whoever runs the script would make the draw unre-derivable."""
    assert AUDIT_SEED == 20260731


def test_chart_questions_are_the_forced_type() -> None:
    """Recorded as a test because the reason is specific to charts, not a general policy.

    A chart answer is the only kind with no text-layer corroboration available: the values
    can be checked against the cited crop, but not which year or series each belongs to.
    """
    assert ALWAYS_AUDITED == frozenset({"chart_value_trend"})
