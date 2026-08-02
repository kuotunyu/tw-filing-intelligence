"""The report must refuse to be written when it would leave something out.

Every test here is a way of omitting something -- a denominator, a limitation, a failed gate,
the lock hash -- and every one must raise rather than produce a shorter, better-reading
document. A report that omits is worse than one that fails to build, because only the second
one tells you.
"""

from __future__ import annotations

from typing import Any

import pytest
from scripts.make_report import LIMITATIONS as STUDY_LIMITATIONS
from scripts.make_report import _report_lock_sha256

from twfi.eval.report import (
    REQUIRED_LIMITATIONS,
    MissingContent,
    build,
    format_proportion,
    gate_table,
)

GATES: list[dict[str, Any]] = [
    {"gate": "G1", "name": "data reproducible", "kind": "hard", "passed": True, "detail": "ok"},
    {
        "gate": "G4",
        "name": "citation validity",
        "kind": "hard",
        "passed": False,
        "detail": "below the 90% threshold",
        "observed": ["81.8% (27/33, 95% CI 65.6%-91.4%)"],
    },
]

COMPOSITION = {
    "records": 33,
    "fully_human": 19,
    "answer_model_drafted": 7,
    "question_model_chosen": 9,
    "needs_audit": 14,
    "audited": 10,
    "trustworthy": 29,
}

SUMMARY: dict[str, Any] = {
    "baseline": "F0",
    "candidate": "F7",
    "factors": {
        "F0": {"overall_accuracy": {"n": 33, "correct": 12}},
        "F7": {"overall_accuracy": {"n": 33, "correct": 21}},
    },
}

LIMITATIONS = {key: f"{heading}: 具體說明。" for key, heading in REQUIRED_LIMITATIONS}


def report(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "verdict": "NO_GO",
        "gates": GATES,
        "summary": SUMMARY,
        "composition": COMPOSITION,
        "limitations": LIMITATIONS,
        "protocol_lock_sha256": "0" * 64,
        "findings": ["表格抽取在 locked 財報頁上 20 個目標只載入 2 個。"],
    }
    base.update(overrides)
    return build(**base)


# ------------------------------------------------------------- it builds at all


def test_a_complete_report_builds() -> None:
    text = report()
    assert "# 可行性報告" in text
    assert "不是投資建議" in text, "CLAUDE.md rule 10 requires this on any output"


def test_report_generator_supplies_every_required_limitation() -> None:
    required = {key for key, _heading in REQUIRED_LIMITATIONS}

    assert required <= set(STUDY_LIMITATIONS)
    assert all(STUDY_LIMITATIONS[key].strip() for key in required)


def test_report_requires_the_non_independent_approval_disclosure() -> None:
    assert "approval_process" in {key for key, _heading in REQUIRED_LIMITATIONS}


def test_numeric_coverage_limitation_describes_the_registered_broad_store() -> None:
    limitation = STUDY_LIMITATIONS["numeric_coverage"]

    assert "numeric_broad.duckdb" in limitation
    assert "不看 gold" in limitation
    assert "只涵蓋 **gold 有問到的 account**" not in limitation


def test_numeric_ambiguity_limitation_uses_the_preserved_source_ref_measurement() -> None:
    limitation = STUDY_LIMITATIONS["numeric_ambiguity"]

    assert "46/115（40.0%）" in limitation
    assert "32/34（94.1%）" in limitation
    assert "source_ref" in limitation


def test_report_uses_the_lock_digest_that_g9_verified_in_summary() -> None:
    digest = "a" * 64
    lock_payload = {"protocol_version": "1.0.0", "frozen_at": "now", "entries": []}

    assert _report_lock_sha256({"protocol_lock_sha256": digest}, lock_payload) == digest


def test_two_runs_produce_the_same_bytes() -> None:
    """Nothing reads a clock, so a report is reproducible from its inputs."""
    assert report() == report()


# ---------------------------------------------- a percentage without its denominator


def test_a_bare_rate_cannot_be_printed() -> None:
    with pytest.raises(MissingContent, match="without its denominator"):
        format_proportion({"rate": 0.67}, where="x")


def test_a_summary_with_a_bare_rate_refuses_to_become_a_report() -> None:
    broken = {
        **SUMMARY,
        "factors": {"F0": {"overall_accuracy": {"rate": 0.36}}, "F7": SUMMARY["factors"]["F7"]},
    }
    with pytest.raises(MissingContent, match="denominator"):
        report(summary=broken)


def test_every_printed_rate_carries_n_and_an_interval() -> None:
    text = report()
    assert "(12/33" in text and "(21/33" in text
    assert text.count("95% CI") >= 2


def test_the_overlap_warning_is_not_conditional() -> None:
    """It must not be printed only when the result is unflattering."""
    assert "區間重疊" in report()
    assert "區間重疊" in report(verdict="GO")


# ------------------------------------------------------------------- limitations


@pytest.mark.parametrize("key", [key for key, _ in REQUIRED_LIMITATIONS])
def test_dropping_any_required_limitation_raises(key: str) -> None:
    partial = {k: v for k, v in LIMITATIONS.items() if k != key}
    with pytest.raises(MissingContent, match=key):
        report(limitations=partial)


def test_an_empty_limitation_counts_as_missing() -> None:
    """Satisfying the requirement with whitespace is the obvious way round it."""
    with pytest.raises(MissingContent):
        report(limitations={**LIMITATIONS, "chart_route": "   "})


def test_the_limitations_are_separate_entries_not_one_paragraph() -> None:
    """One paragraph could mention sample size and quietly drop the rest."""
    assert len(REQUIRED_LIMITATIONS) >= 5
    text = report()
    for _key, heading in REQUIRED_LIMITATIONS:
        assert heading in text


# --------------------------------------------------------------- negative results


def test_a_failed_gate_appears_in_the_table() -> None:
    text = report()
    assert "G4" in text
    assert "FAIL" in text
    assert "81.8%" in text, "the observed value must be shown, not just the verdict"


def test_no_go_carries_the_protocol_prohibition() -> None:
    text = report(verdict="NO_GO")
    assert "不得" in text
    assert "最小的下一個研究問題" in text


def test_conditional_go_says_it_is_a_resource_result() -> None:
    text = report(verdict="CONDITIONAL_GO")
    assert "資源結果" in text


def test_a_verdict_the_gate_evaluator_cannot_produce_is_refused() -> None:
    """No path may print a verdict run_gate did not reach."""
    with pytest.raises(MissingContent, match="not one the gate evaluator produces"):
        report(verdict="PROBABLY_FINE")


def test_gates_are_ordered_by_id_not_by_outcome() -> None:
    """Grouping failures last would let a reader skim the passes and stop."""
    shuffled = [GATES[1], GATES[0]]
    assert gate_table(shuffled).index("G1") < gate_table(shuffled).index("G4")


def test_gates_sort_numerically_so_g4_precedes_g10() -> None:
    """A string sort gives G1, G10, G2. The first version did exactly that.

    The test above passed under lexicographic order too, because G1 precedes G4 either way --
    reading the generated report is what exposed it.
    """
    numbered = [
        {"gate": f"G{n}", "name": "x", "kind": "hard", "passed": True, "detail": ""}
        for n in (10, 2, 4, 1)
    ]
    table = gate_table(numbered)
    positions = [table.index(f"| G{n} ") for n in (1, 2, 4, 10)]
    assert positions == sorted(positions), "gates must read G1, G2, G4, G10"


def test_no_gates_at_all_is_refused() -> None:
    with pytest.raises(MissingContent, match="no verdict"):
        gate_table([])


# ------------------------------------------------------------------- provenance


def test_a_report_without_a_lock_hash_is_refused() -> None:
    """A result untied to a frozen protocol is not a pre-registered result."""
    with pytest.raises(MissingContent, match="pre-registered"):
        report(protocol_lock_sha256=None)


def test_the_lock_hash_is_printed() -> None:
    assert "0" * 64 in report()


# -------------------------------------------------------------- gold composition


def test_the_composition_must_be_printed_whole() -> None:
    with pytest.raises(MissingContent, match="missing"):
        report(composition={k: v for k, v in COMPOSITION.items() if k != "audited"})


def test_an_absent_composition_is_refused() -> None:
    with pytest.raises(MissingContent, match="D-019"):
        report(composition={})


def test_the_audit_rate_is_computed_and_shown() -> None:
    assert "10/14 (71%)" in report()


def test_findings_are_included_when_given() -> None:
    assert "20 個目標只載入 2 個" in report()


def test_a_pipe_in_a_detail_does_not_break_the_table() -> None:
    """A gate detail is prose from another module and may contain anything."""
    piped = [{**GATES[0], "detail": "a | b", "observed": ["c | d"]}]
    table = gate_table(piped)
    for line in table.splitlines()[2:]:
        assert line.count("|") - line.count("\\|") == 5, line
