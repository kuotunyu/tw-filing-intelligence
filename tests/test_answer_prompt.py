"""The prompt and the answer contract.

Worth testing carefully for a specific reason: a parsing bug here would surface as an *answer*
failure. If `parse_answer` mislaid the 單位 line, unit accuracy would drop and the drop would be
attributed to whichever retrieval factor happened to be under test.
"""

from __future__ import annotations

from twfi.answer.generate import Generation
from twfi.answer.prompt import build_prompt, parse_answer
from twfi.index.retrieve import Hit

WELL_FORMED = """答案：530,738,356
單位：千元
期間：FY2023
依據：[2][3]"""


def hit(index: int, *, doc: str = "1301-FY2023-AR", pages: tuple[int, ...] = (188,)) -> Hit:
    return Hit(
        chunk_index=index,
        score=1.0,
        chunk_id=f"{doc}:struct:{index:05d}",
        doc_id=doc,
        pages=pages,
        text=f"段落內容 {index}",
    )


# ------------------------------------------------------------------- the prompt


def test_passages_are_numbered_so_citations_can_point_at_them() -> None:
    prompt = build_prompt("資產總計是多少？", [hit(0), hit(1)])
    assert "[1]" in prompt and "[2]" in prompt


def test_each_passage_carries_its_document_and_page() -> None:
    """A citation that cannot be resolved to a page is not checkable evidence."""
    prompt = build_prompt("q", [hit(0, pages=(188,))])
    assert "1301-FY2023-AR" in prompt
    assert "188" in prompt


def test_a_multi_page_chunk_lists_both_pages() -> None:
    assert "187、188" in build_prompt("q", [hit(0, pages=(187, 188))])


def test_refusal_is_offered_explicitly() -> None:
    """G7 and G8 score refusal; a prompt that never mentions it measures reluctance instead."""
    assert "無法回答" in build_prompt("q", [hit(0)])


def test_retrieving_nothing_still_produces_a_usable_prompt() -> None:
    """A rung that found nothing must still be asked, so its refusal can be scored."""
    prompt = build_prompt("q", [])
    assert "沒有檢索到" in prompt
    assert "無法回答" in prompt


def test_the_question_reaches_the_prompt() -> None:
    assert "台塑民國112年度的資產總計" in build_prompt(
        "台塑民國112年度的資產總計是多少？", [hit(0)]
    )


# -------------------------------------------------------------------- parsing


def test_a_well_formed_answer_splits_into_its_fields() -> None:
    draft = parse_answer(WELL_FORMED)
    assert draft.answer == "530,738,356"
    assert draft.unit == "千元"
    assert draft.period == "FY2023"
    assert draft.cited == (2, 3)


def test_absent_markers_become_none_rather_than_a_unit_called_none() -> None:
    draft = parse_answer("答案：是\n單位：無\n期間：無\n依據：[1]")
    assert draft.unit is None
    assert draft.period is None


def test_halfwidth_colons_parse_too() -> None:
    draft = parse_answer("答案: 12\n單位: %\n期間: FY2023\n依據: [1]")
    assert draft.answer == "12"
    assert draft.unit == "%"


def test_a_completion_ignoring_the_format_still_yields_an_answer() -> None:
    """Returning nothing would record a formatting failure as a retrieval failure."""
    draft = parse_answer("資產總計是 530,738,356 千元。")
    assert draft.answer == "資產總計是 530,738,356 千元。"
    assert draft.cited == ()


def test_a_missing_line_loses_one_field_not_the_answer() -> None:
    draft = parse_answer("答案：530,738,356\n依據：[1]")
    assert draft.answer == "530,738,356"
    assert draft.unit is None
    assert draft.cited == (1,)


def test_surrounding_chatter_does_not_break_the_fields() -> None:
    draft = parse_answer(
        "好的，以下是結果：\n答案：12.5\n單位：%\n期間：FY2023\n依據：[1]\n希望有幫助"
    )
    assert draft.answer == "12.5"
    assert draft.unit == "%"


def test_a_refusal_parses_as_the_answer() -> None:
    draft = parse_answer("答案：無法回答\n單位：無\n期間：無\n依據：")
    assert draft.answer == "無法回答"
    assert draft.cited == ()


# ------------------------------------------------------------- citation resolution


def test_citations_resolve_to_the_hits_they_name() -> None:
    hits = [hit(0), hit(1), hit(2)]
    resolved = parse_answer(WELL_FORMED).cited_hits(hits)
    assert [h.chunk_index for h in resolved] == [1, 2]


def test_an_out_of_range_citation_is_dropped_rather_than_raising() -> None:
    """Citing [9] when given three passages is a citation error, not a crash."""
    draft = parse_answer("答案：x\n單位：無\n期間：無\n依據：[9]")
    assert draft.cited == (9,)
    assert draft.cited_hits([hit(0), hit(1), hit(2)]) == ()


def test_a_zero_citation_is_dropped() -> None:
    """Indices are 1-based; [0] would silently mean the last passage under Python indexing."""
    draft = parse_answer("答案：x\n單位：無\n期間：無\n依據：[0]")
    assert draft.cited_hits([hit(0), hit(1)]) == ()


# ------------------------------------------------------------------ generation


def test_a_failed_generation_is_not_an_empty_answer() -> None:
    """A call that failed and a model that answered nothing are different findings."""
    failed = Generation(
        text="", prompt_tokens=0, completion_tokens=0, seconds=1.0, model="m", error="boom"
    )
    assert not failed.ok
    assert failed.to_json()["error"] == "boom"


def test_a_successful_generation_reports_its_tokens() -> None:
    """A latency without token counts cannot be compared to anything (protocol 3.6)."""
    payload = Generation(
        text="ok", prompt_tokens=3557, completion_tokens=512, seconds=14.2, model="m"
    ).to_json()
    assert payload["prompt_tokens"] == 3557
    assert payload["completion_tokens"] == 512
    assert payload["seconds"] == 14.2
