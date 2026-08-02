"""The chart route: F5 (caption into the index) and F6 (answer from the crop pixels).

Two rungs that look adjacent and are deliberately kept apart. A caption makes a figure
*findable*; only the pixels make it *answerable*. Protocol 2.4 states the separation as a
requirement -- 「caption 只進 index；最終數值必須來自 original crop pixels 或可靠結構化資料」 --
and :mod:`twfi.chart.crop_answer` enforces it by not accepting a caption at all.
"""

from twfi.chart.caption import Caption, caption_figure, captions_to_blocks
from twfi.chart.crop_answer import ChartAnswer, answer_from_crop

__all__ = [
    "Caption",
    "caption_figure",
    "captions_to_blocks",
    "ChartAnswer",
    "answer_from_crop",
]
