"""Decide which route a question takes, and say why.

Protocol 2.4 requires a typed router emitting ``narrative | numeric | chart | cross_modal |
metadata | unanswerable``, each decision carrying a ``reason`` and a ``confidence``. Protocol 3.5
scores route accuracy against the mapping from ``question_type``.

**Rules, not a model.** Three arguments, in order of weight:

1. A model router would put a second generative component inside the thing under test. Its
   mistakes would land in F7's number with nothing to attribute them to, which is the same
   argument :mod:`twfi.index.lexical` makes for not using a segmenter.
2. Routing is a *bounded* decision over six labels, and protocol 2.4 caps the pipeline at one
   correction. A rule set is auditable against that cap; a model is not.
3. It is deterministic and free, so route accuracy is a property of the study rather than of
   whichever model happened to be resident.

**``metadata`` is in the output space and is never emitted.** Protocol 3.5 says the locked set has
no metadata questions and that a router emitting it is always scored wrong. Keeping it in the
Literal but unreachable makes that explicit: the label exists so the confusion matrix has a column
for a mistake this router cannot currently make.

Confidence is a coarse three-level signal, not a calibrated probability. Calling 0.7 a probability
would invite calibration analysis the rule set cannot support -- there is no distribution behind
it, only "this matched a strong cue" versus "this is where unmatched questions go".
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from twfi.protocol import ROUTE_BY_QUESTION_TYPE, Route

__all__ = [
    "RouteDecision",
    "classify",
    "confusion_matrix",
    "route_accuracy",
    "effective_routes",
    "gold_route_of",
]

#: Protocol 3.5's mapping is the specification, and it does not group questions the way intuition
#: does. `table_cell` maps to **chart**, not numeric -- the protocol calls that rung the
#: "chart/table route" and it reads values out of *rendered* structures, tables included. `numeric`
#: is for figures the system must **compute**: `numeric_calculation` and `cross_period_comparison`.
#: Getting this backwards scored 20% on dev because every table lookup went to numeric.
#:
#: A question needing arithmetic: a ratio, a growth rate, a difference between periods.
_COMPUTED = re.compile(r"比率|比重|佔|占|成長|年增|增減|變動(?:比例|幅度)?|相比|差異|減去|合計佔")
#: A question reading a value that is printed -- in a table or on a chart.
_PRINTED = re.compile(r"多少|金額|總計|總額|營收|營業收入|營業成本|淨利|每股|資產|負債|權益|盈餘")
#: A question about a picture specifically.
_CHART = re.compile(r"圖|走勢|趨勢|柱狀|折線|圓餅|座標|圖例|示意")
#: A question naming more than one filing.
_CROSS_DOCUMENT = re.compile(r"年報與|財報與|兩份|不同文件|跨文件|與.*報告(?:書)?(?:相比|對照)")
#: Any period mention. Two of them is the signature of `cross_period_comparison`, which
#: protocol 3.5 maps to **numeric** -- 「民國111年度與民國112年度分別是多少」 asks for a
#: comparison even though every word in it says "look this up".
_PERIOD = re.compile(r"(?:民國)?\d{2,3}\s*年(?:\s*\d{1,2}\s*月\s*\d{1,2}\s*日|度)?|FY\d{4}")

#: A question about the document rather than its contents.
_METADATA = re.compile(r"文件(?:名稱|類型|日期)|報告(?:期間|年度)為何|由誰簽核|會計師事務所名稱")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Where a question goes, why, and how sure the rules are."""

    route: Route
    reason: str
    confidence: float

    def to_json(self) -> dict[str, object]:
        return {"route": self.route, "reason": self.reason, "confidence": self.confidence}


def classify(question: str) -> RouteDecision:
    """Route one question, following protocol 3.5's type-to-route mapping.

    Precedence, and why: a question that both computes and mentions a chart is ``chart``, because
    the value has to be read off the picture before anything can be computed with it. A question
    that computes over printed figures is ``numeric``. A plain lookup of a printed figure is
    ``chart``, which is the protocol's name for the chart/table route.

    ``unanswerable`` is deliberately never returned. Whether a question can be answered is a fact
    about the *evidence*, not about the wording -- deciding it here would settle G7 and G8 in the
    router, before anything has been retrieved. :func:`route_accuracy` therefore takes an optional
    post-hoc refusal signal, which is how the pipeline actually assigns that label.
    """
    if _METADATA.search(question):
        return RouteDecision("metadata", "asks about the document rather than its contents", 0.6)

    chart = bool(_CHART.search(question))
    computed = bool(_COMPUTED.search(question))
    printed = bool(_PRINTED.search(question))

    if chart:
        return RouteDecision("chart", "names a picture, so the value must be read off it", 0.7)
    if _CROSS_DOCUMENT.search(question):
        return RouteDecision("cross_modal", "names more than one filing", 0.6)
    if len(set(_PERIOD.findall(question))) >= 2:
        return RouteDecision(
            "numeric", "names two periods, so it is a comparison rather than a lookup", 0.7
        )
    if computed:
        return RouteDecision("numeric", "needs arithmetic over printed figures", 0.7)
    if printed:
        return RouteDecision("chart", "reads a value printed in a table", 0.6)
    return RouteDecision("narrative", "no figure or chart cue; read the prose", 0.5)


def gold_route_of(question_type: str) -> str:
    """The route protocol 3.5 maps a question type to."""
    return ROUTE_BY_QUESTION_TYPE[question_type]


def effective_routes(
    decisions: Sequence[RouteDecision], refused: Sequence[bool] | None = None
) -> list[str]:
    """The routes as the pipeline finally labelled them.

    ``unanswerable`` is a post-hoc label: the router cannot know from the wording whether the
    corpus holds the answer, so the pipeline assigns it when it refuses. Passing ``refused``
    reports the routes the system actually ended on; omitting it reports the router in isolation.
    """
    if refused is None:
        return [decision.route for decision in decisions]
    return [
        "unanswerable" if declined else decision.route
        for decision, declined in zip(decisions, refused, strict=True)
    ]


def route_accuracy(
    decisions: Sequence[RouteDecision],
    question_types: Sequence[str],
    refused: Sequence[bool] | None = None,
) -> float:
    """Share of questions routed to the route their type maps to."""
    if not decisions:
        return 0.0
    routes = effective_routes(decisions, refused)
    correct = sum(
        1
        for route, kind in zip(routes, question_types, strict=True)
        if route == gold_route_of(kind)
    )
    return correct / len(decisions)


def confusion_matrix(
    decisions: Sequence[RouteDecision],
    question_types: Sequence[str],
    refused: Sequence[bool] | None = None,
) -> Mapping[tuple[str, str], int]:
    """``(gold route, predicted route) -> count``.

    Protocol 3.5 asks for the matrix and not only the accuracy, because the two mistakes a router
    can make are not equally bad: sending a numeric question to narrative loses the SQL path,
    while sending a narrative one to numeric merely wastes a refused lookup.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for route, kind in zip(effective_routes(decisions, refused), question_types, strict=True):
        counts[gold_route_of(kind), route] += 1
    return counts
