"""The local generation call, at the decoding parameters protocol 2.2 freezes.

Through ollama's HTTP API rather than its CLI, because the frozen parameters have to be set
exactly and the CLI does not expose all of them. ``httpx`` is already a declared dependency.

Every call returns its token counts and its wall time alongside the text. Protocol 3.6 asks for
generation p50/p95 and prompt/completion tokens, and a latency reported without the token counts
cannot be compared to anything -- 14 seconds for 512 output tokens and 14 seconds for 20 are
different findings about the same number.

Nothing here reaches the network beyond ``127.0.0.1``. The tests never call it: the runner takes
the generation function as an argument, so the whole answer path is exercised offline against a
fake.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

__all__ = ["DECODING", "Generation", "GenerationConfig", "generate"]

#: Protocol 2.2, frozen before the locked run. ``think=false`` is separate from these because
#: ollama takes it at the top level rather than inside ``options``.
DECODING: dict[str, Any] = {
    "temperature": 0.0,
    "top_p": 1.0,
    "top_k": 1,
    "seed": 20260731,
    "num_predict": 512,
    "num_ctx": 8192,
}


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    """Where to generate and with what. Every field lands in the run's telemetry."""

    model: str = "qwen3.6:27b"
    base_url: str = "http://127.0.0.1:11434"
    #: A single answer must not take longer than G10's whole budget for one.
    timeout_seconds: float = 180.0
    options: dict[str, Any] = field(default_factory=lambda: dict(DECODING))


@dataclass(frozen=True, slots=True)
class Generation:
    """One completion, with what protocol 3.6 needs to report about it."""

    text: str
    prompt_tokens: int
    completion_tokens: int
    seconds: float
    model: str
    #: Set when the call failed. The text is then empty and the caller records a failure rather
    #: than scoring an empty string as a wrong answer -- those are different findings.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_json(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "seconds": round(self.seconds, 3),
            "model": self.model,
            "error": self.error,
        }


def generate(prompt: str, config: GenerationConfig | None = None) -> Generation:
    """Run one completion locally and time it.

    Returns a :class:`Generation` carrying an ``error`` rather than raising, because a ladder run
    over dozens of questions must report which ones failed at the end instead of stopping at the
    first timeout -- and because a failed call and a wrong answer must not be counted as the
    same thing.
    """
    settings = config or GenerationConfig()
    started = time.perf_counter()
    try:
        with httpx.Client(base_url=settings.base_url, timeout=settings.timeout_seconds) as client:
            response = client.post(
                "/api/generate",
                json={
                    "model": settings.model,
                    "prompt": prompt,
                    "stream": False,
                    "think": False,
                    "options": settings.options,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return Generation(
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            seconds=time.perf_counter() - started,
            model=settings.model,
            error=f"{type(exc).__name__}: {exc}",
        )
    return Generation(
        text=str(payload.get("response", "")).strip(),
        prompt_tokens=int(payload.get("prompt_eval_count") or 0),
        completion_tokens=int(payload.get("eval_count") or 0),
        seconds=time.perf_counter() - started,
        model=settings.model,
    )
