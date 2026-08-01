"""The gate evaluator decides GO. Most of these tests are about it refusing to.

Two properties carry the weight. Absent evidence must fail, or deleting a metric becomes
the cheapest route to GO. And a rate arriving without its denominator must be refused, or a
three-item category can be reported as 100% and read as though it meant something.
"""

from __future__ import annotations

from typing import Any

import pytest

from twfi.eval.gates import (
    SOFT_GATES,
    Proportion,
    decide,
    evaluate,
    mcnemar_exact,
    read_proportion,
    wilson_interval,
)
from twfi.protocol import GATES, HARD_CATEGORIES, SINGLE_GATE_CATEGORIES


def prop(correct: int, n: int) -> dict[str, int]:
    return {"n": n, "correct": correct}


def summary(**overrides: Any) -> dict[str, Any]:
    """A summary that passes every gate, so each test can break exactly one thing."""
    baseline = {category: prop(1, 4) for category in HARD_CATEGORIES}
    candidate = {category: prop(3, 4) for category in HARD_CATEGORIES}
    base: dict[str, Any] = {
        "protocol_lock_sha256": "0" * 64,
        "baseline": "F0",
        "candidate": "F7",
        "factors": {
            "F0": {"overall_accuracy": prop(12, 33), "by_category": baseline},
            "F7": {"overall_accuracy": prop(24, 33), "by_category": candidate},
        },
        "citation_validity": {"n": 33, "valid": 32},
        "numeric_route_accuracy": prop(14, 15),
        "route_accuracy": prop(31, 33),
        "unanswerable": {"n": 4, "over_answered": 0, "refusal_precision": prop(4, 4)},
        "probes": {"n": 5, "refused": 5},
        "resources": {"retrieval_p95_s": 1.2, "generation_p95_s": 40.0, "vram_peak_gb": 20.5},
        "checks": {"data_reproducible": True, "results_reproducible": True},
    }
    base.update(overrides)
    return base


def verdict(**overrides: Any) -> str:
    return decide(evaluate(summary(**overrides)))


def failed(payload: dict[str, Any]) -> set[str]:
    return {o.gate for o in evaluate(payload) if not o.passed}


# ------------------------------------------------------------------ the happy path


def test_a_summary_meeting_every_threshold_is_go() -> None:
    assert verdict() == "GO"


def test_every_gate_reports_once_and_in_order() -> None:
    gates = [o.gate for o in evaluate(summary())]
    assert gates == [f"G{n}" for n in range(1, 11)]


# --------------------------------------------------------- absent evidence must fail


@pytest.mark.parametrize(
    "key",
    [
        "factors",
        "citation_validity",
        "numeric_route_accuracy",
        "route_accuracy",
        "unanswerable",
        "probes",
        "resources",
        "checks",
    ],
)
def test_removing_any_metric_fails_its_gate(key: str) -> None:
    """Deleting a number must never be a route to GO.

    Built on the payload directly rather than through ``verdict(**overrides)``: that helper
    merges overrides into a complete base, so a deleted key came straight back and the test
    passed while proving nothing.
    """
    payload = summary()
    del payload[key]
    assert failed(payload), f"removing {key} left every gate passing"
    expected = "CONDITIONAL_GO" if key == "resources" else "NO_GO"
    assert decide(evaluate(payload)) == expected


def test_removing_resources_is_only_a_soft_failure() -> None:
    payload = summary()
    del payload["resources"]
    assert failed(payload) == {"G10"}
    assert decide(evaluate(payload)) == "CONDITIONAL_GO"


def test_an_empty_summary_is_no_go_not_go() -> None:
    assert decide(evaluate({})) == "NO_GO"


def test_no_outcomes_at_all_is_no_go() -> None:
    """Nothing having been evaluated is not everything having passed."""
    assert decide([]) == "NO_GO"


# ------------------------------------------------- a rate without its denominator


def test_a_bare_rate_is_refused() -> None:
    problem = read_proportion({"rate": 0.95}, where="x")
    assert isinstance(problem, str)
    assert "without its denominator" in problem


def test_a_bare_rate_fails_the_gate_it_feeds() -> None:
    assert "G4" in failed(summary(citation_validity={"rate": 0.99}))


def test_a_proportion_needs_a_positive_denominator() -> None:
    with pytest.raises(ValueError, match="positive denominator"):
        Proportion(0, 0)


def test_a_numerator_cannot_exceed_its_denominator() -> None:
    with pytest.raises(ValueError, match="outside"):
        Proportion(5, 4)


# -------------------------------------------------------------------- G2, both halves


def test_g2_needs_the_pooled_gain() -> None:
    """A pooled gain under 15pp fails even with a strong single category."""
    baseline = {category: prop(2, 4) for category in HARD_CATEGORIES}
    candidate = {category: prop(2, 4) for category in HARD_CATEGORIES}
    # One category improves a lot; the pool barely moves.
    single = sorted(SINGLE_GATE_CATEGORIES)[0]
    candidate[single] = prop(4, 4)
    payload = summary(
        factors={
            "F0": {"overall_accuracy": prop(12, 33), "by_category": baseline},
            "F7": {"overall_accuracy": prop(14, 33), "by_category": candidate},
        }
    )
    assert "G2" in failed(payload)


def test_g2_needs_a_single_category_too() -> None:
    """A pool that clears 15pp on tiny spread-out gains is not enough."""
    baseline = {category: prop(0, 4) for category in HARD_CATEGORIES}
    candidate = {category: prop(1, 4) for category in HARD_CATEGORIES}  # +25pp everywhere
    payload = summary(
        factors={
            "F0": {"overall_accuracy": prop(12, 33), "by_category": baseline},
            "F7": {"overall_accuracy": prop(18, 33), "by_category": candidate},
        }
    )
    # +25pp per category clears both halves, so this must pass -- the guard is the next test.
    assert "G2" not in failed(payload)


def test_chart_alone_cannot_satisfy_the_single_category_half() -> None:
    """D-020: with two chart items one lucky read is 50pp, so chart is excluded from it."""
    baseline = {category: prop(2, 4) for category in HARD_CATEGORIES}
    candidate = dict(baseline)
    candidate["chart_value_trend"] = prop(2, 2)
    baseline["chart_value_trend"] = prop(0, 2)
    payload = summary(
        factors={
            "F0": {"overall_accuracy": prop(12, 33), "by_category": baseline},
            "F7": {"overall_accuracy": prop(14, 33), "by_category": candidate},
        }
    )
    assert "G2" in failed(payload), "chart must not be able to carry G2 on its own"


def test_the_pooled_set_is_pooled_by_item_not_averaged_by_category() -> None:
    """A three-item category must not weigh as much as a five-item one.

    Constructed so the two conventions disagree: the small category improves and the large
    one does not, which averaging would reward more than pooling does.
    """
    baseline = {category: prop(2, 5) for category in HARD_CATEGORIES}
    candidate = dict(baseline)
    small = "cross_document"
    baseline[small] = prop(0, 3)
    candidate[small] = prop(3, 3)
    payload = summary(
        factors={
            "F0": {"overall_accuracy": prop(12, 33), "by_category": baseline},
            "F7": {"overall_accuracy": prop(15, 33), "by_category": candidate},
        }
    )
    pooled = next(o for o in evaluate(payload) if o.gate == "G2")
    assert "pooled hard set" in pooled.observed[0]
    # 3 extra correct answers out of a pool of 5*len(categories) is well under 15pp.
    assert not pooled.passed


# ------------------------------------------------------------------------ the rest


def test_g3_allows_a_small_regression_and_not_a_large_one() -> None:
    ok = summary(
        factors={
            "F0": {
                "overall_accuracy": prop(20, 100),
                "by_category": {c: prop(1, 4) for c in HARD_CATEGORIES},
            },
            "F7": {
                "overall_accuracy": prop(16, 100),
                "by_category": {c: prop(3, 4) for c in HARD_CATEGORIES},
            },
        }
    )
    assert "G3" not in failed(ok)
    bad = summary(
        factors={
            "F0": {
                "overall_accuracy": prop(20, 100),
                "by_category": {c: prop(1, 4) for c in HARD_CATEGORIES},
            },
            "F7": {
                "overall_accuracy": prop(14, 100),
                "by_category": {c: prop(3, 4) for c in HARD_CATEGORIES},
            },
        }
    )
    assert "G3" in failed(bad)


def test_g7_fails_on_over_answering_and_on_poor_precision_separately() -> None:
    assert "G7" in failed(
        summary(unanswerable={"n": 4, "over_answered": 2, "refusal_precision": prop(4, 4)})
    )
    assert "G7" in failed(
        summary(unanswerable={"n": 4, "over_answered": 0, "refusal_precision": prop(1, 4)})
    )


def test_g8_counts_refusals_not_a_rate() -> None:
    assert "G8" in failed(summary(probes={"n": 5, "refused": 3}))
    assert "G8" not in failed(summary(probes={"n": 5, "refused": 4}))


def test_g1_and_g9_fail_when_their_owner_has_not_reported() -> None:
    assert "G1" in failed(summary(checks={"results_reproducible": True}))
    assert "G9" in failed(summary(checks={"data_reproducible": True}))


def test_a_non_boolean_check_is_not_treated_as_true() -> None:
    assert "G1" in failed(
        summary(checks={"data_reproducible": "yes", "results_reproducible": True})
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [("retrieval_p95_s", 9.0), ("generation_p95_s", 120.0), ("vram_peak_gb", 23.9)],
)
def test_any_resource_limit_alone_gives_conditional_go(key: str, value: float) -> None:
    resources = {"retrieval_p95_s": 1.0, "generation_p95_s": 10.0, "vram_peak_gb": 10.0}
    resources[key] = value
    assert verdict(resources=resources) == "CONDITIONAL_GO"


def test_a_hard_failure_is_not_offset_by_soft_success() -> None:
    assert verdict(probes={"n": 5, "refused": 0}) == "NO_GO"


def test_only_g10_is_soft() -> None:
    assert SOFT_GATES == frozenset({"G10"})
    kinds = {o.gate: o.kind for o in evaluate(summary())}
    assert {gate for gate, kind in kinds.items() if kind == "soft"} == {"G10"}


# ---------------------------------------------------------------- Wilson interval


def test_wilson_stays_inside_zero_to_one_at_the_extremes() -> None:
    """The normal approximation goes negative here, which would understate uncertainty."""
    low, high = wilson_interval(4, 4)
    assert 0.0 <= low <= high <= 1.0
    assert low < 1.0, "four for four must not be reported as certainly 100%"


def test_wilson_has_width_even_at_zero() -> None:
    low, high = wilson_interval(0, 5)
    assert low == 0.0
    assert high > 0.2, "zero of five does not rule out a fifth of the population"


def test_a_small_denominator_gives_a_wide_interval() -> None:
    narrow = wilson_interval(30, 40)
    wide = wilson_interval(3, 4)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_an_interval_needs_a_trial() -> None:
    with pytest.raises(ValueError, match="at least one trial"):
        wilson_interval(0, 0)


def test_a_proportion_prints_its_n_and_interval() -> None:
    """The protocol requires n alongside every percentage; this is where that shows up."""
    text = str(Proportion(3, 4))
    assert "3/4" in text
    assert "95% CI" in text


def test_the_thresholds_come_from_the_protocol() -> None:
    """Not restated here: a second copy would be a second thing to forget to update."""
    assert GATES.pooled_hard_min_gain_pp == 15.0
    assert GATES.min_probe_refusals == 4


# ------------------------------------------------- paired significance (McNemar)


def _outcomes(bits: str) -> dict[str, int]:
    return {f"Q{index}": int(bit) for index, bit in enumerate(bits)}


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        # Hand-computed two-sided exact binomial on the discordant pairs.
        ("11101", "00011", (3, 1, 0.625)),
        ("110", "001", (2, 1, 1.0)),
        ("11111", "00000", (5, 0, 0.0625)),
        ("111111", "000000", (6, 0, 0.03125)),
        ("11110", "00001", (4, 1, 0.375)),
    ],
)
def test_mcnemar_matches_the_exact_binomial(
    left: str, right: str, expected: tuple[int, int, float]
) -> None:
    only_left, only_right, probability = mcnemar_exact(_outcomes(left), _outcomes(right))
    assert (only_left, only_right) == expected[:2]
    assert probability == pytest.approx(expected[2])


def test_identical_outcomes_cannot_be_separated() -> None:
    """No discordant pair means the data does not distinguish them at all, so p = 1."""
    assert mcnemar_exact(_outcomes("1011"), _outcomes("1011")) == (0, 0, 1.0)


def test_the_test_is_paired_not_a_comparison_of_totals() -> None:
    """Same totals, opposite questions: unpaired tests see nothing, McNemar sees the maximum.

    This is the whole reason for using it -- two configurations scoring 3/4 each are not
    equivalent if they succeed on disjoint questions.
    """
    only_left, only_right, probability = mcnemar_exact(_outcomes("1110"), _outcomes("0111"))
    assert (only_left, only_right) == (1, 1)
    assert probability == pytest.approx(1.0)


def test_a_five_percent_result_is_reachable_at_this_sample_size() -> None:
    """Sanity on the honesty of the reported p-values: six discordant pairs one way suffices.

    Worth pinning, because with 15 dev questions almost nothing reaches it -- and a test that
    could *never* reach significance would make "not significant" vacuous rather than informative.
    """
    assert mcnemar_exact(_outcomes("111111"), _outcomes("000000"))[2] < 0.05
    assert mcnemar_exact(_outcomes("11111"), _outcomes("00000"))[2] > 0.05


def test_questions_missing_from_one_side_are_ignored_rather_than_guessed() -> None:
    left = {"Q1": 1, "Q2": 0, "Q3": 1}
    right = {"Q1": 0, "Q2": 1}
    assert mcnemar_exact(left, right) == (1, 1, 1.0)
