"""Generation and the answer contract: turning retrieved evidence into a citable answer."""

from twfi.answer.generate import Generation, GenerationConfig, generate
from twfi.answer.prompt import AnswerDraft, build_prompt, parse_answer

__all__ = [
    "AnswerDraft",
    "Generation",
    "GenerationConfig",
    "build_prompt",
    "generate",
    "parse_answer",
]
