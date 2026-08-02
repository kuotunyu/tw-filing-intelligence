"""Choose the completion path for one factor-ladder question."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from twfi.answer.generate import Generation, GenerationConfig, generate
from twfi.answer.prompt import AnswerDraft, parse_answer
from twfi.chart.crop_answer import REFUSAL, ChartAnswer
from twfi.numeric.route import NumericAnswer
from twfi.protocol import Route

__all__ = ["AnswerResult", "complete_answer"]


@dataclass(frozen=True, slots=True)
class AnswerResult:
    """The response plus the route that actually handled it."""

    completion: Generation
    draft: AnswerDraft
    handled_route: Route


def complete_answer(
    prompt: str,
    config: GenerationConfig,
    *,
    dispatch: bool,
    decision_route: Route,
    numeric: NumericAnswer | None,
    chart: ChartAnswer | None,
    generate_fn: Callable[[str, GenerationConfig | None], Generation] = generate,
) -> AnswerResult:
    """Apply F0-F6 fallthrough or F7's selected-route commitment.

    Before F7, a route adds capability and a miss falls through to the shared narrative
    generator.  At F7, selecting numeric or chart commits to that route: its refusal is the
    result, otherwise dispatch mistakes would remain free and F7 would not test routing.
    """
    if numeric is not None and numeric.ok:
        completion = Generation(numeric.as_text(), 0, 0, 0.0, "deterministic-sql")
        draft = replace(parse_answer(numeric.as_text()), unit=numeric.unit, period=numeric.period)
        return AnswerResult(completion, draft, "numeric")

    if chart is not None and chart.ok:
        completion = Generation(
            chart.value,
            chart.prompt_tokens,
            chart.completion_tokens,
            chart.seconds,
            chart.model,
        )
        draft = replace(parse_answer(chart.value), answer=chart.value, unit=chart.unit)
        return AnswerResult(completion, draft, "chart")

    if dispatch and decision_route in {"numeric", "chart"}:
        text = numeric.as_text() if decision_route == "numeric" and numeric is not None else REFUSAL
        completion = Generation(text, 0, 0, 0.0, f"{decision_route}-refusal")
        return AnswerResult(completion, parse_answer(text), decision_route)

    completion = generate_fn(prompt, config)
    handled_route: Route = decision_route if dispatch else "narrative"
    return AnswerResult(completion, parse_answer(completion.text), handled_route)
