"""F7 dispatch must make a selected route's failure observable."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from twfi.answer.generate import Generation, GenerationConfig
from twfi.eval.answers import is_refusal
from twfi.eval.dispatch import complete_answer
from twfi.numeric.route import NumericAnswer


def _backend(text: str = "fallback answer") -> Any:
    calls: list[str] = []

    def generate(prompt: str, _config: Any = None) -> Generation:
        calls.append(prompt)
        return Generation(text, 10, 3, 1.0, "fake")

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


def _failed_numeric() -> NumericAnswer:
    return NumericAnswer(None, None, "FY2024", refusal="template miss")


def test_dispatched_numeric_failure_refuses_without_falling_back_to_generation() -> None:
    backend = _backend()

    result = complete_answer(
        "prompt",
        GenerationConfig(),
        dispatch=True,
        decision_route="numeric",
        numeric=_failed_numeric(),
        chart=None,
        generate_fn=backend,
    )

    assert is_refusal(result.draft.answer)
    assert result.handled_route == "numeric"
    assert backend.calls == []


def test_pre_f7_numeric_failure_still_falls_through_as_the_ladder_defines() -> None:
    backend = _backend("有證據的文字答案")

    result = complete_answer(
        "prompt",
        GenerationConfig(),
        dispatch=False,
        decision_route="numeric",
        numeric=_failed_numeric(),
        chart=None,
        generate_fn=backend,
    )

    assert result.draft.answer == "有證據的文字答案"
    assert result.handled_route == "narrative"
    assert backend.calls == ["prompt"]


def test_successful_numeric_route_records_numeric_as_the_handler() -> None:
    backend = _backend()
    numeric = NumericAnswer(
        Decimal("42"),
        "千元",
        "FY2024",
        source_refs=("source",),
    )

    result = complete_answer(
        "prompt",
        GenerationConfig(),
        dispatch=True,
        decision_route="numeric",
        numeric=numeric,
        chart=None,
        generate_fn=backend,
    )

    assert result.draft.answer == "42"
    assert result.handled_route == "numeric"
    assert backend.calls == []
