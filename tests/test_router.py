"""Typed routing, and protocol 3.5's route accuracy.

The mapping these test against is the protocol's, not intuition's. `table_cell` routes to
**chart** -- the protocol calls that rung the chart/table route and it reads values out of
rendered structures, tables included -- while `numeric` is for figures the system must compute.
Reading it the other way scored 20% on dev, so the mapping gets its own test.
"""

from __future__ import annotations

from typing import get_args

import pytest

from twfi.protocol import ROUTE_BY_QUESTION_TYPE, Route
from twfi.router.classify import (
    RouteDecision,
    classify,
    confusion_matrix,
    effective_routes,
    gold_route_of,
    route_accuracy,
)

# ------------------------------------------------------------------ the mapping


def test_a_table_lookup_routes_to_the_chart_table_rung() -> None:
    """Protocol 3.5: table_cell -> chart. Not numeric, however much it looks like one."""
    assert gold_route_of("table_cell") == "chart"
    assert classify("台塑民國112年度的資產總計是多少？").route == "chart"


def test_a_computation_routes_to_numeric() -> None:
    assert classify("台塑民國112年度的負債總計佔資產總計的比率是多少？").route == "numeric"


def test_two_periods_make_it_a_comparison() -> None:
    """「分別是多少」 asks for a comparison even though every word says "look this up"."""
    decision = classify("台塑的資產總計，民國111年度與民國112年度分別是多少？")
    assert decision.route == "numeric"
    assert "two periods" in decision.reason


def test_a_chart_question_routes_to_chart() -> None:
    assert classify("台積電產能計劃圖中民國112年的年成長率是多少？").route == "chart"


def test_prose_routes_to_narrative() -> None:
    assert classify("台塑民國112年度是否有現金不足額情形？").route == "narrative"


def test_two_filings_route_to_cross_modal() -> None:
    assert classify("台積電的年報與財報兩份文件對照後，數字是否一致？").route == "cross_modal"


# --------------------------------------------------- what the router will not decide


def test_the_router_never_returns_unanswerable() -> None:
    """Whether a question can be answered is a fact about the evidence, not the wording.

    Deciding it here would settle G7 and G8 in the router, before anything has been retrieved.
    """
    questions = [
        "台塑民國113年度的資產總計是多少？",
        "台塑民國112年度的碳排放強度是多少？",
        "台塑民國112年度是否有現金不足額情形？",
    ]
    assert all(classify(q).route != "unanswerable" for q in questions)


def test_the_refusal_signal_supplies_the_unanswerable_label() -> None:
    decisions = [classify("台塑民國112年度的資產總計是多少？")] * 2
    assert effective_routes(decisions, [False, True]) == ["chart", "unanswerable"]


def test_route_accuracy_without_a_refusal_signal_reports_the_router_alone() -> None:
    decisions = [RouteDecision("numeric", "", 0.7)]
    assert route_accuracy(decisions, ["numeric_calculation"]) == 1.0
    assert route_accuracy(decisions, ["unanswerable"]) == 0.0


# ------------------------------------------------------------------- reporting


def test_every_decision_carries_a_reason_and_a_confidence() -> None:
    """Protocol 2.4 requires both; a route with no reason cannot be audited."""
    decision = classify("台塑民國112年度的資產總計是多少？")
    assert decision.reason
    assert 0.0 < decision.confidence <= 1.0


def test_the_confusion_matrix_separates_the_two_kinds_of_mistake() -> None:
    """Sending numeric to narrative loses the SQL path; the reverse wastes a refused lookup."""
    decisions = [RouteDecision("narrative", "", 0.5), RouteDecision("numeric", "", 0.7)]
    matrix = confusion_matrix(decisions, ["numeric_calculation", "narrative_fact"])
    assert matrix[("numeric", "narrative")] == 1
    assert matrix[("narrative", "numeric")] == 1


def test_accuracy_of_an_empty_run_is_zero_rather_than_an_error() -> None:
    assert route_accuracy([], []) == 0.0


def test_every_emitted_route_is_in_the_protocol_vocabulary() -> None:
    allowed = set(get_args(Route))
    questions = [
        "台塑民國112年度的資產總計是多少？",
        "台塑民國112年度的負債總計佔資產總計的比率是多少？",
        "台積電產能計劃圖的年成長率？",
        "台塑是否有現金不足額情形？",
        "年報與財報兩份文件是否一致？",
    ]
    assert {classify(q).route for q in questions} <= allowed


def test_every_question_type_maps_to_a_route_the_router_could_emit() -> None:
    """Except unanswerable, which the router deliberately cannot reach -- see the module note."""
    reachable = set(ROUTE_BY_QUESTION_TYPE.values()) - {"unanswerable", "metadata"}
    assert reachable <= set(get_args(Route))


@pytest.mark.parametrize(
    ("question_type", "route"),
    [
        ("narrative_fact", "narrative"),
        ("table_cell", "chart"),
        ("numeric_calculation", "numeric"),
        ("cross_period_comparison", "numeric"),
        ("chart_value_trend", "chart"),
        ("cross_page", "narrative"),
        ("cross_document", "cross_modal"),
        ("unanswerable", "unanswerable"),
    ],
)
def test_the_protocol_mapping_is_what_the_document_says(question_type: str, route: str) -> None:
    """Pinned literally, because misreading it is what produced a 20% router."""
    assert gold_route_of(question_type) == route
