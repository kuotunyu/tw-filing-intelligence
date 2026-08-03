"""The chart route, offline.

Protocol 2.4 splits this route in two and the split is the thing worth testing: a caption makes a
figure findable, and only the pixels make it answerable. Every test here runs against a fake
backend -- nothing reaches ollama, a GPU, or the network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.synthetic_pdf import build_filing
from twfi.answer.generate import Generation
from twfi.chart.caption import CAPTION_PROMPT, Caption, caption_figure, captions_to_blocks
from twfi.chart.crop_answer import (
    CROP_ANSWER_PROMPT,
    REFUSAL,
    answer_from_crop,
    parse_chart_answer,
)
from twfi.parsing.figures import Figure
from twfi.parsing.types import BBox


def fake(text: str, *, error: str = "") -> Any:
    """A generation backend that records what it was given."""
    calls: list[dict[str, Any]] = []

    def run(prompt: str, config: Any = None, *, images: Any = None) -> Generation:
        calls.append({"prompt": prompt, "images": list(images or [])})
        return Generation(
            text=text,
            prompt_tokens=1,
            completion_tokens=1,
            seconds=0.0,
            model=getattr(config, "model", "fake"),
            error=error,
        )

    run.calls = calls  # type: ignore[attr-defined]
    return run


@pytest.fixture()
def filing(tmp_path: Path) -> Path:
    return build_filing(tmp_path / "chart.pdf").path


@pytest.fixture()
def figure() -> Figure:
    return Figure(page=1, bbox=BBox(60, 100, 400, 300), kind="vector", numeric_labels=4)


# ------------------------------------------------------- the contract that matters


def test_the_crop_answerer_has_no_way_to_receive_a_caption() -> None:
    """Protocol 2.4's rule, enforced by the signature rather than by a docstring.

    A comment saying "do not pass the caption" survives until someone is in a hurry. A parameter
    that does not exist cannot be filled in.
    """
    import inspect

    parameters = set(inspect.signature(answer_from_crop).parameters)
    assert "caption" not in parameters
    assert not any("caption" in name for name in parameters)


def test_the_crop_answer_declares_pixels_as_its_provenance(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    backend = fake("數值：35.2\n單位：%\n依據：橫軸112年長條頂端的標籤")
    answer = answer_from_crop(
        "112年的年成長率是多少？", filing, "TEST-DOC", figure, tmp_path, generate_fn=backend
    )
    assert answer.provenance == "crop_pixels"
    assert answer.to_json()["provenance"] == "crop_pixels"


def test_the_crop_actually_reaches_the_model(filing: Path, figure: Figure, tmp_path: Path) -> None:
    """A chart answered without its chart is the failure this route exists to prevent."""
    backend = fake("數值：35.2\n單位：%\n依據：標籤")
    answer_from_crop("問題？", filing, "TEST-DOC", figure, tmp_path, generate_fn=backend)
    sent = backend.calls[0]["images"]
    assert len(sent) == 1
    assert sent[0].suffix == ".png"
    assert sent[0].stat().st_size > 0


def test_the_caption_prompt_forbids_values_and_the_answer_prompt_asks_for_one() -> None:
    """The two prompts pull in opposite directions on purpose."""
    assert "不要寫出圖上的任何數值" in CAPTION_PROMPT
    assert "數值：" in CROP_ANSWER_PROMPT


# ------------------------------------------------------------------ F5: captions


def test_a_caption_carries_the_provenance_protocol_2_4_requires(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    """crop page, bbox, captioning model and source document."""
    caption = caption_figure(
        filing, "TEST-DOC", figure, tmp_path, generate_fn=fake("產能規劃圖，橫軸為年度。")
    )
    payload = caption.to_json()
    assert payload["doc_id"] == "TEST-DOC"
    assert payload["page"] == 1
    assert "crop" in payload["crop_ref"]
    assert payload["caption_model"]
    assert caption.ok


def test_a_caption_says_it_was_generated(filing: Path, figure: Figure, tmp_path: Path) -> None:
    """An index that cannot tell generated text from extracted text cannot be audited."""
    caption = caption_figure(filing, "TEST-DOC", figure, tmp_path, generate_fn=fake("營收組成圖。"))
    assert "described by" in caption.index_text()


def test_a_failed_caption_is_kept_rather_than_raised(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    """One unreadable figure must not abort an index build over several hundred."""
    caption = caption_figure(
        filing, "TEST-DOC", figure, tmp_path, generate_fn=fake("", error="timeout")
    )
    assert not caption.ok
    assert caption.error == "timeout"


def test_a_failed_caption_does_not_enter_the_index(figure: Figure) -> None:
    """An empty index entry is retrievable noise that can outrank a real chunk."""
    failed = Caption("D", 1, "p1:crop:0,0,1,1", "", "m", error="timeout")
    assert captions_to_blocks([failed], [figure]) == ()


def test_captions_and_figures_must_line_up(figure: Figure) -> None:
    """A caption on the wrong bbox is a citation pointing at the wrong picture."""
    good = Caption("D", 1, "p1:crop:0,0,1,1", "圖", "m")
    with pytest.raises(ValueError, match="positional"):
        captions_to_blocks([good, good], [figure])


def test_a_caption_block_is_indexable(figure: Figure) -> None:
    caption = Caption("D", 1, "p1:crop:0,0,1,1", "產能規劃圖", "m")
    blocks = captions_to_blocks([caption], [figure])
    assert len(blocks) == 1
    assert blocks[0].kind == "figure"
    assert blocks[0].bbox == figure.bbox


# --------------------------------------------------------------- F6: reading the crop


def test_a_read_value_is_parsed_into_its_fields() -> None:
    value, unit, basis = parse_chart_answer("數值：35.2\n單位：%\n依據：長條頂端標籤")
    assert (value, unit) == ("35.2", "%")
    assert basis == "長條頂端標籤"


def test_no_unit_reads_as_no_unit() -> None:
    _, unit, _ = parse_chart_answer("數值：12\n單位：無\n依據：圖例")
    assert unit is None


def test_free_prose_is_a_refusal_rather_than_a_guess() -> None:
    """Picking an unlabelled number out of prose is how 「看不出來」 becomes a figure."""
    value, unit, _ = parse_chart_answer("這張圖看起來大概是 35% 左右吧")
    assert value == ""
    assert unit is None


def test_an_unreadable_chart_refuses(filing: Path, figure: Figure, tmp_path: Path) -> None:
    """Refusing on a chart that does not print the value is a correct answer under G7/G8."""
    backend = fake(f"數值：{REFUSAL}\n單位：無\n依據：圖上只有座標軸沒有標數字")
    answer = answer_from_crop("問題？", filing, "TEST-DOC", figure, tmp_path, generate_fn=backend)
    assert answer.refused
    assert not answer.ok


def test_a_generation_failure_is_reported_not_raised(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    backend = fake("", error="connection refused")
    answer = answer_from_crop("問題？", filing, "TEST-DOC", figure, tmp_path, generate_fn=backend)
    assert answer.error == "connection refused"
    assert not answer.ok


def test_an_unrenderable_figure_is_reported_not_raised(filing: Path, tmp_path: Path) -> None:
    """Page 999 does not exist; the run must continue and say so."""
    absent = Figure(page=999, bbox=BBox(0, 0, 10, 10), kind="vector")
    answer = answer_from_crop(
        "問題？", filing, "TEST-DOC", absent, tmp_path, generate_fn=fake("數值：1")
    )
    assert answer.error
    assert not answer.ok


def test_the_answer_cites_the_crop_it_read(filing: Path, figure: Figure, tmp_path: Path) -> None:
    """A chart answer that cannot say which crop it came from is not evidence."""
    backend = fake("數值：35.2\n單位：%\n依據：標籤")
    answer = answer_from_crop("問題？", filing, "TEST-DOC", figure, tmp_path, generate_fn=backend)
    assert "TEST-DOC" in answer.citation()
    assert answer.crop_ref in answer.citation()
    assert answer.crop_page == 1
    assert answer.bbox == figure.bbox.as_tuple()


def test_the_chart_answer_preserves_generation_telemetry(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    def measured(_prompt: str, config: Any = None, *, images: Any = None) -> Generation:
        return Generation(
            text="值：35.2\n單位：%\n依據：圖中數值標籤",
            prompt_tokens=17,
            completion_tokens=9,
            seconds=1.25,
            model=getattr(config, "model", "fake"),
        )

    answer = answer_from_crop(
        "年成長率？", filing, "TEST-DOC", figure, tmp_path, generate_fn=measured
    )

    assert answer.prompt_tokens == 17
    assert answer.completion_tokens == 9
    assert answer.seconds == 1.25
    assert answer.to_json()["seconds"] == 1.25


def test_the_question_reaches_the_model_and_nothing_else_does(
    filing: Path, figure: Figure, tmp_path: Path
) -> None:
    """Protocol 2.4 allows the crop and reliable structured data. Not surrounding prose."""
    backend = fake("數值：35.2\n單位：%\n依據：標籤")
    question = "台積電產能計劃圖中民國112年的年成長率是多少？"
    answer_from_crop(question, filing, "TEST-DOC", figure, tmp_path, generate_fn=backend)
    prompt = backend.calls[0]["prompt"]
    assert question in prompt
    assert prompt == CROP_ANSWER_PROMPT.format(question=question)
