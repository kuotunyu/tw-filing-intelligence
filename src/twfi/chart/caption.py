"""F5: describe a figure well enough to retrieve it, and never well enough to quote.

Protocol 2.4: 「caption 只進 index」. A caption exists so that 「產能計劃圖」 finds the page
holding the capacity chart. It is a *model's description of pixels*, so a number inside it has
been through a generation step that nothing checked -- reading a value out of it would report a
hallucination with a page citation attached.

**Why the VLM writes these at all.** :mod:`twfi.parsing.figures` extracts printed captions and
found 0, 0 and 1 across three annual reports carrying 122, 457 and 181 detected figures (D-006).
These filings label their charts inconsistently or not at all, so an index built on extracted
captions would leave essentially every figure unfindable.

**The number that appears in a caption is a liability, not a feature.** The prompt asks for the
subject, the axes and the series -- what makes a figure *findable* -- and asks for values to be
left out. That is a reduction in what the caption can do, chosen on purpose: a caption without
figures cannot be mistaken for evidence. :func:`twfi.chart.crop_answer.answer_from_crop` enforces
the same rule structurally by having no parameter a caption could be passed to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from twfi.answer.generate import GenerationConfig, generate
from twfi.parsing.figures import FigureConfig, render_crop
from twfi.parsing.types import Block

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from twfi.answer.generate import Generation
    from twfi.parsing.figures import Figure

__all__ = ["CAPTION_PROMPT", "Caption", "caption_figure", "captions_to_blocks"]

#: Describe, do not read. The final sentence is the one doing the work: a caption carrying
#: 「112年為35%」 invites exactly the shortcut protocol 2.4 forbids.
CAPTION_PROMPT = """你看到的是一份台灣上市公司年報中的一張圖表裁切圖。

請用一到兩句繁體中文描述**這張圖在講什麼**，讓人日後能用關鍵字找到它。要寫出：
- 主題（例如：產能規劃、營收組成、資本支出）
- 座標軸或分類項目的名稱
- 有哪些資料數列或圖例

**不要寫出圖上的任何數值。** 這段描述只用於檢索，不會被當成答案來源；
寫進數字只會讓它看起來像證據。若無法判斷這是什麼圖，就回答「無法判讀」。

描述："""


@dataclass(frozen=True, slots=True)
class Caption:
    """A generated description of one figure, and where it came from.

    Protocol 2.4 requires the crop page, the bbox, the captioning model and the source document
    to be kept. They are on the object rather than in a log because the index entry has to be
    able to say which model wrote it -- a caption is generated text, and an index that cannot
    distinguish generated text from extracted text cannot be audited.
    """

    doc_id: str
    page: int
    crop_ref: str
    text: str
    model: str
    #: Empty when the call succeeded. A failed caption is kept rather than dropped so the index
    #: build can report how many figures it could not describe.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)

    def index_text(self) -> str:
        """What goes into the retrieval index for this figure."""
        return f"{self.text}\n[chart crop at {self.crop_ref}, described by {self.model}]"

    def to_json(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "page": self.page,
            "crop_ref": self.crop_ref,
            "text": self.text,
            "caption_model": self.model,
            "error": self.error,
        }


def caption_figure(
    pdf_path: Path,
    doc_id: str,
    figure: Figure,
    crop_dir: Path,
    *,
    config: GenerationConfig | None = None,
    figure_config: FigureConfig | None = None,
    generate_fn: Callable[..., Generation] = generate,
) -> Caption:
    """Render one figure and describe it, for the index only.

    ``generate_fn`` is injected so the tests exercise this offline against a fake backend; the
    default is the real local call. A rendering or generation failure comes back as a
    :class:`Caption` carrying ``error`` rather than raising, so one unreadable figure does not
    abort an index build over several hundred of them.
    """
    settings = config or GenerationConfig()
    destination = (
        crop_dir / f"{doc_id}_p{figure.page}_{figure.bbox.x0:.0f}_{figure.bbox.y0:.0f}.png"
    )
    try:
        crop = render_crop(pdf_path, figure, destination, figure_config)
    except Exception as exc:  # ParsingError, and anything pymupdf raises through it
        return Caption(
            doc_id=doc_id,
            page=figure.page,
            crop_ref=figure.crop_ref,
            text="",
            model=settings.model,
            error=f"{type(exc).__name__}: {exc}",
        )

    result = generate_fn(CAPTION_PROMPT, settings, images=[crop])
    return Caption(
        doc_id=doc_id,
        page=figure.page,
        crop_ref=figure.crop_ref,
        text=result.text.strip(),
        model=settings.model,
        error=result.error,
    )


def captions_to_blocks(captions: Sequence[Caption], figures: Sequence[Figure]) -> tuple[Block, ...]:
    """Turn successful captions into indexable blocks.

    Failed ones are dropped here rather than indexed as empty text: an index entry with no
    content is retrievable noise that can outrank a real chunk.

    Raises:
        ValueError: If the two sequences do not line up. They are positional, and a caption
            silently attached to the wrong figure would put a description on the wrong bbox --
            which is a citation pointing at the wrong picture.
    """
    if len(captions) != len(figures):
        raise ValueError(
            f"{len(captions)} caption(s) for {len(figures)} figure(s); they are positional "
            "and a mismatch would attach a description to the wrong crop"
        )
    return tuple(
        Block(
            page=caption.page,
            kind="figure",
            text=caption.index_text(),
            bbox=figure.bbox,
            order=index,
        )
        for index, (caption, figure) in enumerate(zip(captions, figures, strict=True))
        if caption.ok
    )
