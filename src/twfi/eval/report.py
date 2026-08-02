"""Build the feasibility report, and refuse to build one that omits what it must say.

The report is the study's only output, so the failure to guard against is not a typo -- it is
a report that reads well because it left something out. Three omissions are specifically
easy and specifically fatal:

* **A percentage without its denominator.** Protocol 4 says a rate reported without ``n``
  makes the report incomplete. With categories of two to six items, one item is 17 to 50
  percentage points, so a bare "67%" invites a reader to believe something the data cannot
  support. Every proportion this module prints carries ``n``, its numerator, and a Wilson
  interval, and :func:`build` raises if asked to print one that cannot.
* **A missing limitations section.** Protocol 4 requires specific admissions -- that the
  sample supports direction and not effect size, that the chart questions come from one
  company's two pages, and that the broad numeric store is filtered rather than complete.
  A report is not allowed to be silent about any of them, so each is a required section and
  its absence is an error rather than an omission someone might notice.
* **A hidden negative result.** CLAUDE.md rule 3: NO_GO and CONDITIONAL_GO go in the report
  unaltered. So the verdict is written from ``GO_NO_GO.json`` and the gate table lists every
  gate that failed with its observed values. There is no code path that prints a verdict the
  gate evaluator did not produce.
* **A promised but absent next question.** Protocol 4 allows a non-GO study to proceed only by
  naming one independently testable question that addresses its main failure. Saying that such
  a question appears "below" is not enough; :func:`build` requires and prints the actual text.

Pure: it takes data and returns markdown. Nothing here reads a file or a clock, so the whole
document is testable, and two runs over the same inputs produce the same bytes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from twfi.eval.gates import read_proportion
from twfi.protocol import FACTOR_IDS

__all__ = [
    "REQUIRED_LIMITATIONS",
    "MissingContent",
    "build",
    "format_proportion",
    "gate_table",
]


class MissingContent(Exception):
    """Raised when the report would omit something the protocol requires it to state."""


#: Each key must appear in the limitations mapping handed to :func:`build`, with non-empty
#: prose. They are separate entries rather than one paragraph so that a report cannot satisfy
#: the requirement by mentioning sample size and quietly dropping the rest.
REQUIRED_LIMITATIONS: Final[tuple[tuple[str, str], ...]] = (
    ("sample_size", "What the sample can and cannot support"),
    ("chart_route", "What the two chart questions actually measure"),
    ("numeric_coverage", "Why the numeric store holds what it holds"),
    ("structured_source", "Why this says verified rather than official structured data"),
    ("parser_generality", "What was not tested: learned layout models"),
    # Added 2026-08-01 from measurement, before the freeze -- the only time it could be added.
    # Both development-set filings have damaged text layers (17.9% and 15.4% of characters
    # decoding to the wrong script, 48% and 43% of pages), so every threshold and setting chosen
    # on dev was chosen on damaged documents. Protocol "文字層誠實性" states what the report must
    # say; this entry is what stops the report being written without saying it.
    ("text_layer", "That both development-set filings have damaged text layers"),
    # Added 2026-08-01 from measurement, before the freeze. The 15 dev questions cover four
    # distinct (document, pageset) targets, one chunk carries eight of them, DEV-0011 is
    # unreachable so every dev retrieval rate has a 14/15 ceiling, and no measured difference
    # reaches p<0.05. Protocol "DEV 集合的聚集性" states what the report must say.
    ("dev_clustering", "That dev's 15 questions cover only four distinct evidence targets"),
    # Added 2026-08-02 from measurement, before the freeze. `numeric_coverage` says what the store
    # holds; this says whether what it holds is right, which is a different claim and the one a
    # report is likelier to drop. Ingesting the whole corpus rather than the cells gold names
    # (D-044/D-052) showed the account name is not a key: 40% of locked keys carry conflicting
    # values -- 2882 is 94%, its notes repeating 資產總計 for every subsidiary. Each source_ref is
    # now preserved so require() refuses those candidates rather than letting the last page win.
    # Dev is at 0% after reading consolidated-vs-parent-only off the page heading, so a dev figure
    # does not license a claim about locked.
    ("numeric_ambiguity", "That an account name is not a unique key in a filing"),
    # D-048/D-050 were approved by delegating judgement to the implementer after dev results had
    # already been seen, not by an independent reviewer blind to those results. The protocol says
    # this disclosure must survive into the report so readers can discount the evidence.
    ("approval_process", "That final pre-freeze approval was not independent or blind"),
)


def format_proportion(payload: Any, *, where: str) -> str:
    """Render a proportion as ``rate (k/n, 95% CI lo-hi)``, or raise.

    Raises:
        MissingContent: If the figure cannot be printed with its denominator. This is the
            one place the rule is enforced, so no caller can print a bare rate by accident.
    """
    parsed = read_proportion(payload, where=where)
    if isinstance(parsed, str):
        raise MissingContent(
            f"{parsed}. Protocol 4 forbids printing a percentage without its denominator, so "
            "this report cannot be written until the summary carries n and a count."
        )
    return str(parsed)


def _gate_order(gate: Mapping[str, Any]) -> tuple[int, str]:
    """Sort key that puts G4 before G10.

    A plain string sort gives G1, G10, G2 -- which is what this did first, and the test only
    checked that G1 preceded G4, a condition lexicographic order happens to satisfy. Reading
    the generated report is what caught it.
    """
    name = str(gate.get("gate", ""))
    digits = "".join(char for char in name if char.isdigit())
    return (int(digits) if digits else 0, name)


def gate_table(gates: Sequence[Mapping[str, Any]]) -> str:
    """A row per gate, failures included and never filtered.

    Sorted by gate number rather than by outcome: grouping failures at the bottom would let a
    reader skim the passes and stop.
    """
    if not gates:
        raise MissingContent("no gates were evaluated, so there is no verdict to report")
    lines = ["| gate | 判定 | 類型 | 說明 |", "|---|---|---|---|"]
    for gate in sorted(gates, key=_gate_order):
        passed = bool(gate.get("passed"))
        mark = "✅ PASS" if passed else "❌ **FAIL**"
        kind = str(gate.get("kind", "hard"))
        detail = str(gate.get("detail", "")).replace("|", "\\|")
        name = str(gate.get("name", ""))
        lines.append(f"| {gate.get('gate')} {name} | {mark} | {kind} | {detail} |")
        for observed in gate.get("observed", ()) or ():
            lines.append(f"| | | | ↳ {str(observed).replace('|', chr(92) + '|')} |")
    return "\n".join(lines)


def _composition_table(composition: Mapping[str, int]) -> str:
    """The gold set's authorship, which D-019 requires the report to print verbatim."""
    if not composition:
        raise MissingContent(
            "gold composition is missing. D-019 requires the report to print how much of the "
            "gold was model-drafted and how much was audited, so a reader can discount it."
        )
    wanted = (
        "records",
        "fully_human",
        "answer_model_drafted",
        "question_model_chosen",
        "needs_audit",
        "audited",
        "trustworthy",
    )
    absent = [key for key in wanted if key not in composition]
    if absent:
        raise MissingContent(f"gold composition is missing {absent}; it must be printed whole")
    lines = ["| 項目 | 數量 |", "|---|---|"]
    for key in wanted:
        lines.append(f"| {key} | {composition[key]} |")
    eligible = composition["needs_audit"]
    if eligible:
        rate = composition["audited"] / eligible
        lines.append(f"| audit rate | {composition['audited']}/{eligible} ({rate:.0%}) |")
    return "\n".join(lines)


def build(
    *,
    verdict: str,
    gates: Sequence[Mapping[str, Any]],
    summary: Mapping[str, Any],
    composition: Mapping[str, int],
    limitations: Mapping[str, str],
    protocol_lock_sha256: str | None,
    findings: Sequence[str] = (),
    next_question: str | None,
) -> str:
    """Render the report, or raise :class:`MissingContent` naming what is absent.

    ``findings`` is where negative and awkward results go. It is not optional in spirit --
    a study that found nothing worth reporting has not looked -- but it is not enforced,
    because inventing a requirement for a minimum number of findings would invite padding.
    """
    if verdict not in {"GO", "CONDITIONAL_GO", "NO_GO"}:
        raise MissingContent(
            f"verdict {verdict!r} is not one the gate evaluator produces; the report may not "
            "state a verdict that run_gate did not reach"
        )
    if verdict != "GO" and not str(next_question or "").strip():
        raise MissingContent(
            "a non-GO report must state one smallest next research question; mentioning the "
            "requirement without printing the question does not satisfy protocol 4"
        )
    for key, heading in REQUIRED_LIMITATIONS:
        if not str(limitations.get(key, "")).strip():
            raise MissingContent(
                f"limitations.{key} ({heading}) is empty. Protocol 4 requires the report to "
                "state this, and a report that omits it is incomplete rather than concise."
            )
    if not protocol_lock_sha256:
        raise MissingContent(
            "no protocol lock hash. A result that cannot be tied to a frozen protocol is not "
            "a pre-registered result, whatever it says at the top of the page."
        )

    baseline = str(summary.get("baseline", "F0"))
    candidate = str(summary.get("candidate", "F7"))
    factors = summary.get("factors")
    if not isinstance(factors, Mapping):
        raise MissingContent("summary.factors is missing; there is nothing to compare")

    parts: list[str] = []
    parts.append("# 可行性報告（⑤A TW Filing Intelligence）\n")
    parts.append(
        "> **這不是投資建議，也不是 production 系統。**\n"
        "> 本文件是一份事前註冊可行性驗證的結果報告，樣本量小，結論的適用範圍見「限制」一節。\n"
    )
    parts.append(f"`protocol_lock_sha256: {protocol_lock_sha256}`\n")

    parts.append(f"## 判定：{verdict}\n")
    if verdict == "NO_GO":
        parts.append(
            "至少一個 hard gate 未通過。依協議 4，**不得**為了通過而修改題目、答案、tolerance "
            "或門檻，也不得刪除這個結果。下方「最小的下一個研究問題」是唯一允許的前進方式。\n"
        )
    elif verdict == "CONDITIONAL_GO":
        parts.append(
            "所有 hard gate 通過，G10（資源）未通過。**這是資源結果，不是能力結果** ——\n"
            "下方 gate 表列出超出的是哪一項限制。\n"
        )
    parts.append(gate_table(gates) + "\n")

    parts.append("## 主要比較\n")
    parts.append(f"Registered baseline: `{baseline}`；candidate: `{candidate}`。\n")
    overall_lines = ["| factor | overall answer accuracy |", "|---|---|"]
    for factor in FACTOR_IDS:
        block = factors.get(factor)
        if not isinstance(block, Mapping):
            raise MissingContent(f"summary.factors.{factor} is missing from the full ladder")
        payload = block.get("overall_accuracy") if isinstance(block, Mapping) else None
        rendered = format_proportion(payload, where=f"factors.{factor}.overall_accuracy")
        overall_lines.append(f"| {factor} | {rendered} |")
    parts.append("\n".join(overall_lines) + "\n")

    candidate_block = factors.get(candidate)
    categories = (
        candidate_block.get("by_category") if isinstance(candidate_block, Mapping) else None
    )
    if not isinstance(categories, Mapping) or not categories:
        raise MissingContent(f"summary.factors.{candidate}.by_category is missing")
    parts.append(f"### {candidate} category accuracy\n")
    category_lines = ["| category | accuracy |", "|---|---|"]
    for category in sorted(categories):
        rendered = format_proportion(
            categories[category], where=f"factors.{candidate}.by_category.{category}"
        )
        category_lines.append(f"| {category} | {rendered} |")
    parts.append("\n".join(category_lines) + "\n")

    unanswerable = summary.get("unanswerable")
    if not isinstance(unanswerable, Mapping):
        raise MissingContent("summary.unanswerable is missing")
    metric_payloads = (
        ("citation validity", summary.get("citation_validity")),
        ("numeric route accuracy", summary.get("numeric_route_accuracy")),
        ("route accuracy", summary.get("route_accuracy")),
        (
            "over-answer rate",
            {"n": unanswerable.get("n"), "correct": unanswerable.get("over_answered")},
        ),
        ("refusal precision", unanswerable.get("refusal_precision")),
        ("no-evidence probes refused", summary.get("probes")),
    )
    parts.append("### Candidate gate proportions\n")
    metric_lines = ["| metric | observed |", "|---|---|"]
    for label, payload in metric_payloads:
        metric_lines.append(f"| {label} | {format_proportion(payload, where=label)} |")
    parts.append("\n".join(metric_lines) + "\n")

    resources = summary.get("resources")
    if not isinstance(resources, Mapping):
        raise MissingContent("summary.resources is missing")
    parts.append("### Resource measurements\n")
    resource_lines = ["| metric | observed |", "|---|---|"]
    for key in ("retrieval_p95_s", "generation_p95_s", "vram_peak_gb"):
        value = resources.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MissingContent(f"summary.resources.{key} is missing or not numeric")
        resource_lines.append(f"| {key} | {float(value):g} |")
    parts.append("\n".join(resource_lines) + "\n")

    parts.append(
        "每個比率都附 n、分子與 Wilson 95% 信賴區間。**區間重疊代表這份樣本無法分辨兩者** ——\n"
        "這一句不因結果好壞而刪除。\n"
    )

    parts.append("## Gold set 組成（D-019 要求逐項印出）\n")
    parts.append(_composition_table(composition) + "\n")
    parts.append(
        "部分 gold 由模型讀渲染頁面起草，並經固定種子的人工抽樣稽核。上表讓讀者自行折扣。\n"
    )

    if findings:
        parts.append("## 發現（含負面結果）\n")
        for finding in findings:
            parts.append(f"- {finding}")
        parts.append("")

    if verdict != "GO":
        parts.append("## 最小的下一個研究問題\n")
        parts.append(str(next_question).strip() + "\n")

    parts.append("## 限制\n")
    for key, heading in REQUIRED_LIMITATIONS:
        parts.append(f"### {heading}\n")
        parts.append(str(limitations[key]).strip() + "\n")

    return "\n".join(parts)
