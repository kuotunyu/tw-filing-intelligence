"""The prompt and the answer contract, identical for every rung of the ladder.

Protocol 2.1 requires the baseline's answer and citation contract to be *the same* as the
candidate's. If F0 were asked for a bare number and F7 for a structured answer with citations,
the comparison between them would be measuring two different tasks and the ladder's deltas would
be unattributable. So there is one prompt builder and one parser here, and the only thing that
differs between rungs is which chunks reach them.

The contract asks for four labelled lines rather than JSON. Local models at
``num_predict=512`` emit malformed JSON often enough that a parse failure would show up as an
answer failure, which would attribute a formatting problem to the retrieval factor under test.
Labelled lines degrade gracefully: a missing line loses one field instead of the whole answer.

Refusal is offered explicitly, and that is load-bearing rather than polite. Gates G7 and G8 score
whether the system declines when the evidence does not support an answer, and a prompt that never
mentions refusal would measure the model's reluctance to guess rather than the pipeline's ability
to detect absence.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from twfi.index.retrieve import Hit

__all__ = [
    "ANSWER_CONTRACT",
    "DEFAULT_VARIANT",
    "PROMPT_VARIANTS",
    "AnswerDraft",
    "build_prompt",
    "parse_answer",
]

#: The four lines the model is asked for. Chinese, because the corpus and the questions are.
ANSWER_CONTRACT = """請只輸出下列四行，不要有其他文字：

答案：<數字或簡短事實；若文件不足以回答，寫「無法回答」>
單位：<例如 千元、%、倍；沒有單位寫「無」>
期間：<例如 FY2023、民國112年度；沒有期間寫「無」>
依據：<引用的段落編號，例如 [2] 或 [1][3]>"""

_FIELD = {
    "answer": re.compile(r"^\s*答案[：:]\s*(?P<value>.*)$", re.MULTILINE),
    "unit": re.compile(r"^\s*單位[：:]\s*(?P<value>.*)$", re.MULTILINE),
    "period": re.compile(r"^\s*期間[：:]\s*(?P<value>.*)$", re.MULTILINE),
    "citations": re.compile(r"^\s*依據[：:]\s*(?P<value>.*)$", re.MULTILINE),
}
_CITATION_INDEX = re.compile(r"\[(\d+)\]")

#: What the model writes when a field does not apply. Mapped to ``None`` rather than kept as the
#: literal string, so ``unit_match`` sees "no unit stated" instead of a unit called 無.
_ABSENT = frozenset({"無", "none", "n/a", "na", "-", "—"})


@dataclass(frozen=True, slots=True)
class AnswerDraft:
    """What the model said, split into the fields protocol 3.3 scores separately."""

    answer: str
    unit: str | None
    period: str | None
    #: 1-based indices into the evidence block, as the model cited them.
    cited: tuple[int, ...]
    raw: str

    def cited_hits(self, hits: Sequence[Hit]) -> tuple[Hit, ...]:
        """The hits the citations point at, dropping indices that name nothing.

        Out-of-range indices are dropped rather than raising: a model citing ``[9]`` when it was
        given five passages has made a citation error, and citation precision is the metric that
        should record it -- not a crash in the runner.
        """
        return tuple(hits[index - 1] for index in self.cited if 1 <= index <= len(hits))


def _clean(value: str) -> str | None:
    stripped = value.strip().strip("。.")
    return None if not stripped or stripped.casefold() in _ABSENT else stripped


#: Instruction variants, tunable on dev (protocol 1.3). Prompt wording is a free parameter and
#: the first measured run showed it is a *large* one: the system refused 8-13 of 15 dev questions
#: when only 2 should be refused, including questions whose answer was printed verbatim in a
#: retrieved passage (D-040).
#:
#: The suspicion these variants test is that 「不得推測」 is being read as forbidding the reading
#: of a value out of a table. They differ only in how the boundary between reading and inferring
#: is described; none of them adds a reasoning step, because protocol 2.2 fixes ``think=false``
#: and states the design must not depend on chain-of-thought.
#:
#: **Choose between them on refusal precision, not on overall correctness.** Overall correctness
#: rewards answering everything, which is the opposite failure and just as wrong.
PROMPT_VARIANTS: dict[str, str] = {
    "strict": (
        "只能根據下列段落回答，不得使用段落以外的知識，也不得推測。\n"
        "若段落不足以回答，請在答案欄寫「無法回答」。"
    ),
    "permissive": (
        "只能根據下列段落回答，不得使用段落以外的知識。\n"
        "段落中的表格數字與敘述就是答案來源，直接讀出對應的值即可 —— 這不算推測。\n"
        "只有在段落中確實找不到所需資訊時，才在答案欄寫「無法回答」。"
    ),
    "verbatim": (
        "只能根據下列段落回答，不得使用段落以外的知識。\n"
        "段落中的表格數字與敘述就是答案來源，直接讀出對應的值即可 —— 這不算推測。\n"
        "答案通常會逐字出現在某一個段落裡，請先找出它。\n"
        "只有在段落中確實找不到所需資訊時，才在答案欄寫「無法回答」。"
    ),
}
DEFAULT_VARIANT = "strict"


def build_prompt(question: str, hits: Sequence[Hit], *, variant: str = DEFAULT_VARIANT) -> str:
    """One prompt, from a question and whatever evidence this rung retrieved.

    Passages are numbered so citations can point at them, and each carries its document and page
    so a citation resolves to something checkable. The chunk text follows its own heading path,
    which the layout chunker already prefixed -- so the model sees the section a passage came
    from without this having to know how chunking works.
    """
    if variant not in PROMPT_VARIANTS:
        raise ValueError(f"unknown prompt variant {variant!r}; have {sorted(PROMPT_VARIANTS)}")
    if not hits:
        evidence = "（沒有檢索到任何段落）"
    else:
        evidence = "\n\n".join(
            f"[{index}] （{hit.doc_id} 第 {'、'.join(str(p) for p in hit.pages) or '?'} 頁）\n"
            f"{hit.text.strip()}"
            for index, hit in enumerate(hits, start=1)
        )
    return (
        "你是臺灣上市公司公開文件的查詢助理。\n"
        f"{PROMPT_VARIANTS[variant]}\n\n"
        f"===== 段落 =====\n{evidence}\n\n"
        f"===== 問題 =====\n{question}\n\n"
        f"===== 輸出格式 =====\n{ANSWER_CONTRACT}\n"
    )


def parse_answer(text: str) -> AnswerDraft:
    """Pull the four contract fields out of a completion.

    A completion that ignored the format entirely still yields an ``answer``: the whole text.
    That is deliberate -- scoring it will fail on its merits, whereas returning an empty answer
    would record a formatting failure as though the pipeline had found nothing.
    """
    found = {name: pattern.search(text) for name, pattern in _FIELD.items()}
    answer_match = found["answer"]
    answer = answer_match.group("value").strip() if answer_match else text.strip()
    citations_match = found["citations"]
    cited = (
        tuple(int(index) for index in _CITATION_INDEX.findall(citations_match.group("value")))
        if citations_match
        else ()
    )
    return AnswerDraft(
        answer=answer,
        unit=_clean(found["unit"].group("value")) if found["unit"] else None,
        period=_clean(found["period"].group("value")) if found["period"] else None,
        cited=cited,
        raw=text,
    )
