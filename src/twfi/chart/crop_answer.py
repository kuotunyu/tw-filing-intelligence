"""F6: read the value off the original crop pixels, and cite the crop.

Protocol 2.4: 「最終數值必須來自 original crop pixels 或可靠結構化資料」.

**The contract is enforced by the signature, not by a comment.** There is no ``caption``
parameter on :func:`answer_from_crop`, so a caller cannot pass one even by mistake, and no
refactor can quietly start feeding one in without changing the function's shape. A rule that
lives only in a docstring is a rule that survives exactly until someone is in a hurry.

Why it matters more here than anywhere else in the study: a caption is a model's prose about a
picture, so a number inside it has already been through one ungrounded generation step. Reading
the value from the caption would produce an answer that is fluent, carries a real page number,
and is a hallucination -- and D-022 records why the text layer cannot be used as a fallback
either, since what identifies a series is the axis label and the legend colour, which is exactly
what the text layer drops.

The answer is parsed into fields rather than returned as prose because protocol 2.4 requires
``{value, unit, crop_page, bbox, model, source_document}``, and because a chart answer that
cannot say which crop it came from is not evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from twfi.answer.generate import GenerationConfig, generate
from twfi.parsing.figures import FigureConfig, render_crop

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable
    from pathlib import Path

    from twfi.answer.generate import Generation
    from twfi.parsing.figures import Figure

__all__ = ["CROP_ANSWER_PROMPT", "ChartAnswer", "answer_from_crop"]

#: Reading a chart has a failure mode reading prose does not: the value is *between* gridlines and
#: the model will interpolate confidently. 「看不出來」 is offered explicitly because a refusal on
#: an unreadable chart is a correct answer, and G7/G8 score it as one.
CROP_ANSWER_PROMPT = """你看到的是一份台灣上市公司年報中的一張圖表裁切圖。

問題：{question}

只根據**這張圖上實際畫出來、標示出來的內容**回答。不要依據常識或推測補齊。
如果圖上沒有標出確切數值，而你只能從座標軸位置推估，請回答「看不出來」。
如果這張圖與問題無關，也回答「看不出來」。

請照以下格式回答，每行一項：
數值：<例如 35.2；看不出來就寫「看不出來」>
單位：<例如 %、億元、千噸；沒有單位寫「無」>
依據：<你從圖上的哪個部分讀到的，例如「橫軸112年對應的長條頂端標籤」>"""

_VALUE = re.compile(r"^\s*數值[：:]\s*(?P<value>.*)$", re.MULTILINE)
_UNIT = re.compile(r"^\s*單位[：:]\s*(?P<value>.*)$", re.MULTILINE)
_BASIS = re.compile(r"^\s*依據[：:]\s*(?P<value>.*)$", re.MULTILINE)

#: What the prompt tells the model to say when the chart does not show the value.
REFUSAL = "看不出來"


@dataclass(frozen=True, slots=True)
class ChartAnswer:
    """One value read off a crop, with the provenance protocol 2.4 requires."""

    value: str
    unit: str | None
    #: How the model says it read the value. Kept because 「橫軸112年對應的長條頂端標籤」 and
    #: 「由座標軸位置估算」 deserve different trust, and only the first is a reading.
    basis: str
    doc_id: str
    crop_page: int
    bbox: tuple[float, float, float, float]
    crop_ref: str
    model: str
    error: str = ""

    #: Fixed. This route has one legitimate source of a value and it is the pixels; the field
    #: exists so a downstream citation check can assert it rather than assume it.
    provenance: str = "crop_pixels"

    @property
    def refused(self) -> bool:
        return self.value == REFUSAL or not self.value

    @property
    def ok(self) -> bool:
        return not self.error and not self.refused

    def citation(self) -> str:
        return f"{self.doc_id} {self.crop_ref} (read from pixels by {self.model})"

    def to_json(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "unit": self.unit,
            "basis": self.basis,
            "source_document": self.doc_id,
            "crop_page": self.crop_page,
            "bbox": list(self.bbox),
            "crop_ref": self.crop_ref,
            "model": self.model,
            "provenance": self.provenance,
            "error": self.error,
        }


def parse_chart_answer(text: str) -> tuple[str, str | None, str]:
    """Pull ``(value, unit, basis)`` out of the model's reply.

    A reply that does not follow the format yields an empty value, which reads as a refusal.
    Guessing at an unlabelled number in free prose is how a 「看不出來」 becomes a figure.
    """
    value_match = _VALUE.search(text)
    unit_match = _UNIT.search(text)
    basis_match = _BASIS.search(text)
    value = value_match.group("value").strip() if value_match else ""
    unit = unit_match.group("value").strip() if unit_match else ""
    basis = basis_match.group("value").strip() if basis_match else ""
    return value, (None if unit in {"", "無"} else unit), basis


def answer_from_crop(
    question: str,
    pdf_path: Path,
    doc_id: str,
    figure: Figure,
    crop_dir: Path,
    *,
    config: GenerationConfig | None = None,
    figure_config: FigureConfig | None = None,
    generate_fn: Callable[..., Generation] = generate,
) -> ChartAnswer:
    """Answer one question from one figure's pixels.

    Note what this does **not** take: a caption, a page of surrounding text, or a retrieved
    chunk. Protocol 2.4 allows the final value to come from the crop or from reliable structured
    data, and nothing else -- so the only thing sent to the model here is the question and the
    image.

    Failures come back on the object rather than as exceptions, matching
    :func:`twfi.answer.generate.generate`: a locked run over several chart questions must report
    which ones failed instead of stopping at the first unreadable page.
    """
    settings = config or GenerationConfig()
    box = figure.bbox.as_tuple()
    destination = crop_dir / f"{doc_id}_p{figure.page}_{box[0]:.0f}_{box[1]:.0f}.png"

    def failed(message: str) -> ChartAnswer:
        return ChartAnswer(
            value="",
            unit=None,
            basis="",
            doc_id=doc_id,
            crop_page=figure.page,
            bbox=box,
            crop_ref=figure.crop_ref,
            model=settings.model,
            error=message,
        )

    try:
        crop = render_crop(pdf_path, figure, destination, figure_config)
    except Exception as exc:  # ParsingError and whatever it wraps
        return failed(f"{type(exc).__name__}: {exc}")

    result = generate_fn(CROP_ANSWER_PROMPT.format(question=question), settings, images=[crop])
    if result.error:
        return failed(result.error)

    value, unit, basis = parse_chart_answer(result.text)
    return ChartAnswer(
        value=value,
        unit=unit,
        basis=basis,
        doc_id=doc_id,
        crop_page=figure.page,
        bbox=box,
        crop_ref=figure.crop_ref,
        model=settings.model,
    )
