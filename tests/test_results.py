"""G9 decides whether summary.json is a report or a claim. These tests are about refusal.

The central property is the one gates.py already commits to: a figure with no raw records
behind it must *fail*, never pass. If absent evidence verified, deleting the records would be
the cheapest way to make a summary reproducible -- and reproducibility is the gate that stops
a hand-typed number reaching a GO.

The second property is quieter and easier to get wrong: nothing must not verify as
everything. An empty summary checked against an empty artifact set has to produce one problem
per required field, not silence.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import pytest

from twfi.errors import ResultIntegrityError
from twfi.eval.results import (
    PROBE_RUN,
    RECORDS_FILENAME,
    RESOURCE_KEYS,
    RESOURCES_FILENAME,
    Problem,
    load_artifacts,
    read_record,
    verify,
)
from twfi.protocol import FACTOR_IDS, ROUTE_BY_QUESTION_TYPE

LOCK: Final = "a" * 64
FACTORS: Final = ("F0", "F7")
RESOURCES: Final[Mapping[str, float]] = {
    "retrieval_p95_s": 1.2,
    "generation_p95_s": 40.0,
    "vram_peak_gb": 20.5,
}

#: Four items rather than 33: the arithmetic under test is counting, and a small ladder makes
#: every expected numerator writable by hand instead of computed by the code being tested.
ITEMS: Final[tuple[tuple[str, str], ...]] = (
    ("LOCK-0001", "table_cell"),
    ("LOCK-0002", "numeric_calculation"),
    ("LOCK-0003", "cross_page"),
    ("LOCK-0004", "unanswerable"),
)


def record(
    question_id: str, category: str, *, factor: str, correct: bool = True, **overrides: Any
) -> dict[str, Any]:
    """One graded record, correct and self-consistent unless a test breaks it on purpose."""
    unanswerable = category == "unanswerable"
    payload: dict[str, Any] = {
        "question_id": question_id,
        "factor": factor,
        "category": category,
        "answerable": not unanswerable,
        "gold_route": ROUTE_BY_QUESTION_TYPE[category],
        "route": ROUTE_BY_QUESTION_TYPE[category],
        "correct": correct,
        # Protocol 4 grades an unanswerable item correct exactly when it was refused.
        "refused": unanswerable and correct,
        "cited_ok": None if unanswerable else correct,
    }
    payload.update(overrides)
    return payload


def probe(question_id: str, *, refused: bool = True) -> dict[str, Any]:
    return {
        "question_id": question_id,
        "factor": "F7",
        "category": "probe",
        "answerable": False,
        "gold_route": "unanswerable",
        "route": "unanswerable",
        "correct": refused,
        "refused": refused,
        "cited_ok": None,
    }


def run(factor: str, *, wrong: Sequence[str] = ()) -> list[dict[str, Any]]:
    return [record(qid, cat, factor=factor, correct=qid not in wrong) for qid, cat in ITEMS]


def artifacts(**overrides: Any) -> dict[str, list[dict[str, Any]]]:
    """F0 gets two items wrong so the two rungs are not accidentally interchangeable."""
    base: dict[str, list[dict[str, Any]]] = {
        "F0": run("F0", wrong=("LOCK-0002", "LOCK-0003")),
        "F7": run("F7"),
        PROBE_RUN: [probe("PROBE-0001"), probe("PROBE-0002")],
    }
    base.update(overrides)
    return base


def summary(**overrides: Any) -> dict[str, Any]:
    """The summary those artifacts actually support, written out by hand."""
    base: dict[str, Any] = {
        "protocol_lock_sha256": LOCK,
        "baseline": "F0",
        "candidate": "F7",
        "factors": {
            "F0": {
                "overall_accuracy": {"n": 4, "correct": 2},
                "by_category": {
                    "table_cell": {"n": 1, "correct": 1},
                    "numeric_calculation": {"n": 1, "correct": 0},
                    "cross_page": {"n": 1, "correct": 0},
                    "unanswerable": {"n": 1, "correct": 1},
                },
            },
            "F7": {
                "overall_accuracy": {"n": 4, "correct": 4},
                "by_category": {category: {"n": 1, "correct": 1} for _, category in ITEMS},
            },
        },
        "citation_validity": {"n": 3, "valid": 3},
        "numeric_route_accuracy": {"n": 1, "correct": 1},
        "route_accuracy": {"n": 4, "correct": 4},
        "unanswerable": {"n": 1, "over_answered": 0, "refusal_precision": {"n": 1, "refused": 1}},
        "probes": {"n": 2, "refused": 2},
        "resources": dict(RESOURCES),
        "checks": {"data_reproducible": True},
    }
    base.update(overrides)
    return base


def check(
    summary_payload: Mapping[str, Any] | None = None,
    raw: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    *,
    lock: str | None = LOCK,
    resources: Mapping[str, Any] | None = RESOURCES,
    factors: Sequence[str] = FACTORS,
) -> tuple[Problem, ...]:
    """Verify the matching pair, unless a test hands in a broken half of it."""
    return verify(
        summary() if summary_payload is None else summary_payload,
        artifacts() if raw is None else raw,
        expected_lock_sha256=lock,
        resources=resources,
        factors=factors,
    )


def only(found: Sequence[Problem], field: str) -> Problem:
    """The one problem about ``field``, so a test can assert on it without ordering luck."""
    matching = [problem for problem in found if problem.field == field]
    assert len(matching) == 1, f"expected exactly one problem about {field}, got {found}"
    return matching[0]


# ------------------------------------------------------------------ the happy path


def test_a_summary_that_matches_its_artifacts_verifies() -> None:
    assert check() == ()


def test_the_two_rungs_are_not_interchangeable_in_the_fixture() -> None:
    """Guards the other tests: if both rungs graded identically, half of them prove nothing."""
    baseline = summary()["factors"]["F0"]["overall_accuracy"]
    candidate = summary()["factors"]["F7"]["overall_accuracy"]
    assert baseline["correct"] != candidate["correct"]


# ----------------------------------------------------------- the summary is not the run


def test_an_inflated_category_is_caught_with_both_numbers_named() -> None:
    payload = summary()
    payload["factors"]["F0"]["by_category"]["numeric_calculation"] = {"n": 1, "correct": 1}
    problem = only(check(payload), "factors.F0.by_category.numeric_calculation")
    assert problem.kind == "mismatch"
    assert "1/1" in problem.claimed
    assert "0/1" in problem.recomputed


def test_an_inflated_overall_accuracy_is_caught() -> None:
    payload = summary()
    payload["factors"]["F0"]["overall_accuracy"] = {"n": 4, "correct": 4}
    problem = only(check(payload), "factors.F0.overall_accuracy")
    assert problem.kind == "mismatch"
    assert "4/4" in problem.claimed
    assert "2/4" in problem.recomputed


def test_a_denominator_that_drops_an_item_is_caught() -> None:
    """3/3 and 3/4 are different claims even when the numerator is honest."""
    payload = summary()
    payload["route_accuracy"] = {"n": 3, "correct": 3}
    problem = only(check(payload), "route_accuracy")
    assert problem.kind == "mismatch"
    assert "3/3" in problem.claimed
    assert "4/4" in problem.recomputed


def test_every_recomputed_metric_can_be_caught_when_wrong() -> None:
    """No summary field is verified only by being present."""
    caught = set()
    for field, payload in (
        ("citation_validity", {"n": 3, "valid": 2}),
        ("numeric_route_accuracy", {"n": 1, "correct": 0}),
        ("route_accuracy", {"n": 4, "correct": 3}),
        ("probes", {"n": 2, "refused": 1}),
    ):
        broken = summary()
        broken[field] = payload
        caught.add(only(check(broken), field).kind)
    assert caught == {"mismatch"}


def test_an_over_answer_count_the_records_contradict_is_caught() -> None:
    payload = summary()
    payload["unanswerable"] = {
        "n": 1,
        "over_answered": 1,
        "refusal_precision": {"n": 1, "refused": 1},
    }
    problem = only(check(payload), "unanswerable.over_answered")
    assert problem.kind == "mismatch"


# --------------------------------------------------------- absent evidence must fail


def test_a_summary_field_with_no_artifacts_behind_it_fails() -> None:
    """The property this module exists for: nothing behind a number is not a pass."""
    raw = artifacts()
    del raw["F0"]
    found = check(raw=raw)
    assert {problem.kind for problem in found} == {"missing_artifacts"}
    assert only(found, "factors.F0.overall_accuracy")
    assert only(found, "factors.F0.by_category.table_cell")


def test_an_empty_run_is_treated_the_same_as_a_missing_one() -> None:
    raw = artifacts(F0=[])
    assert {problem.kind for problem in check(raw=raw)} == {"missing_artifacts"}


def test_a_category_claimed_with_no_records_in_it_fails() -> None:
    payload = summary()
    payload["factors"]["F7"]["by_category"]["cross_document"] = {"n": 3, "correct": 3}
    problem = only(check(payload), "factors.F7.by_category.cross_document")
    assert problem.kind == "missing_artifacts"
    assert "cross_document" in problem.detail


def test_a_category_graded_but_never_reported_fails() -> None:
    """The direction that produces a summary which looks complete."""
    payload = summary()
    del payload["factors"]["F7"]["by_category"]["cross_page"]
    problem = only(check(payload), "factors.F7.by_category.cross_page")
    assert problem.kind == "missing_summary_field"


def test_missing_probe_artifacts_fail_g8s_number() -> None:
    raw = artifacts()
    del raw[PROBE_RUN]
    problem = only(check(raw=raw), "probes")
    assert problem.kind == "missing_artifacts"


def test_refusal_precision_over_no_refusals_is_absent_not_perfect() -> None:
    raw = artifacts(F7=run("F7", wrong=("LOCK-0004",)))
    problem = only(check(raw=raw), "unanswerable.refusal_precision")
    assert problem.kind == "missing_artifacts"
    assert "denominator" in problem.detail


def test_citation_validity_needs_at_least_one_record_in_scope() -> None:
    raw = artifacts(
        F7=[record(qid, cat, factor="F7", cited_ok=None) for qid, cat in ITEMS],
    )
    problem = only(check(raw=raw), "citation_validity")
    assert problem.kind == "missing_artifacts"


def test_unmeasured_resources_fail_rather_than_pass() -> None:
    found = check(resources=None)
    assert {problem.field for problem in found} == {f"resources.{key}" for key in RESOURCE_KEYS}
    assert {problem.kind for problem in found} == {"missing_artifacts"}


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
        "protocol_lock_sha256",
    ],
)
def test_deleting_any_summary_field_fails(key: str) -> None:
    """Built on the payload directly: ``summary(**overrides)`` would put the key back."""
    payload = summary()
    del payload[key]
    found = check(payload)
    assert found, f"deleting {key} left the summary verifying"
    assert any(problem.field.startswith(key) for problem in found)


def test_an_empty_artifact_set_does_not_verify_an_empty_summary() -> None:
    """Nothing is not everything. Every required field must be named as absent."""
    found = verify({}, {}, expected_lock_sha256=LOCK, factors=FACTORS)
    assert {problem.kind for problem in found} == {"missing_summary_field"}
    assert {
        "protocol_lock_sha256",
        "factors",
        "citation_validity",
        "numeric_route_accuracy",
        "route_accuracy",
        "unanswerable",
        "probes",
        "resources",
    } <= {problem.field for problem in found}


def test_a_full_summary_with_no_artifacts_at_all_fails_everywhere() -> None:
    found = verify(summary(), {}, expected_lock_sha256=LOCK, factors=FACTORS)
    assert {problem.kind for problem in found} == {"missing_artifacts"}


# ------------------------------------------------------------------- rates and locks


def test_a_bare_rate_is_refused() -> None:
    payload = summary()
    payload["citation_validity"] = {"rate": 1.0}
    problem = only(check(payload), "citation_validity")
    assert problem.kind == "malformed"
    assert "denominator" in problem.detail


def test_a_bare_rate_inside_a_category_is_refused() -> None:
    payload = summary()
    payload["factors"]["F7"]["by_category"]["table_cell"] = {"rate": 1.0}
    problem = only(check(payload), "factors.F7.by_category.table_cell")
    assert problem.kind == "malformed"


def test_a_lock_hash_mismatch_fails_and_names_both_hashes() -> None:
    problem = only(check(summary(protocol_lock_sha256="b" * 64)), "protocol_lock_sha256")
    assert problem.kind == "lock_mismatch"
    assert problem.claimed == "b" * 64
    assert problem.recomputed == LOCK


def test_no_lock_to_compare_against_is_a_failure_not_a_pass() -> None:
    problem = only(check(lock=None), "protocol_lock_sha256")
    assert problem.kind == "missing_artifacts"


def test_the_factor_ladder_cannot_be_relabelled() -> None:
    found = check(summary(candidate="F3"))
    assert only(found, "candidate").kind == "mismatch"


def test_the_whole_pre_registered_ladder_is_required_by_default() -> None:
    """Protocol 5 step 8 runs F0..F7; two rungs are not a locked run."""
    found = verify(summary(), artifacts(), expected_lock_sha256=LOCK, resources=RESOURCES)
    assert {problem.field for problem in found} == {
        f"factors.{factor}" for factor in FACTOR_IDS if factor not in FACTORS
    }


# ------------------------------------------------- the records are checked, not trusted


def test_a_question_graded_twice_in_one_run_is_refused() -> None:
    """A duplicate inflates a numerator and its denominator together; no rate looks wrong."""
    raw = artifacts()
    raw["F7"] = [*raw["F7"], record("LOCK-0001", "table_cell", factor="F7")]
    problem = only(check(raw=raw), "runs.F7/LOCK-0001")
    assert problem.kind == "inconsistent_artifacts"
    assert "twice" in problem.detail


def test_a_gold_route_the_protocol_does_not_assign_is_refused() -> None:
    raw = artifacts()
    raw["F7"] = [
        record("LOCK-0001", "table_cell", factor="F7", gold_route="narrative"),
        *raw["F7"][1:],
    ]
    problem = only(check(raw=raw), "runs.F7/LOCK-0001")
    assert "protocol 3.5" in problem.detail


def test_a_record_filed_under_the_wrong_factor_is_refused() -> None:
    raw = artifacts()
    raw["F7"] = [record("LOCK-0001", "table_cell", factor="F0"), *raw["F7"][1:]]
    problem = only(check(raw=raw), "runs.F7/LOCK-0001")
    assert problem.kind == "inconsistent_artifacts"


def test_an_unanswerable_item_graded_correct_without_refusing_is_refused() -> None:
    raw = artifacts()
    raw["F7"] = [*raw["F7"][:3], record("LOCK-0004", "unanswerable", factor="F7", refused=False)]
    problem = only(check(raw=raw), "runs.F7/LOCK-0004")
    assert "refused" in problem.detail


def test_an_answerable_flag_contradicting_the_category_is_refused() -> None:
    raw = artifacts()
    raw["F7"] = [record("LOCK-0001", "table_cell", factor="F7", answerable=False), *raw["F7"][1:]]
    assert only(check(raw=raw), "runs.F7/LOCK-0001").kind == "inconsistent_artifacts"


def test_a_probe_carrying_a_gold_category_is_refused() -> None:
    """A probe enrolled in a gold category would move that category's denominator."""
    raw = artifacts()
    raw[PROBE_RUN] = [
        {**probe("PROBE-0001"), "category": "narrative_fact"},
        probe("PROBE-0002"),
    ]
    problem = only(check(raw=raw), f"runs.{PROBE_RUN}/PROBE-0001")
    assert "probe" in problem.detail


def test_a_probe_declared_answerable_is_refused() -> None:
    raw = artifacts()
    raw[PROBE_RUN] = [probe("PROBE-0001"), {**probe("PROBE-0002"), "answerable": True}]
    assert only(check(raw=raw), f"runs.{PROBE_RUN}/PROBE-0002").kind == "inconsistent_artifacts"


def test_rungs_graded_on_different_item_sets_are_refused() -> None:
    """Every proportion can still reproduce while the gain between them means nothing."""
    raw = artifacts()
    raw["F0"] = [*raw["F0"][:3], record("LOCK-0099", "unanswerable", factor="F0")]
    problem = only(check(raw=raw), "runs.F0")
    assert problem.kind == "inconsistent_artifacts"
    assert "LOCK-0004" in problem.detail
    assert "LOCK-0099" in problem.detail


# ------------------------------------------------------------------- record parsing


def test_a_record_missing_cited_ok_is_refused() -> None:
    """Null means "not applicable"; absent means nobody said. They cannot be the same."""
    payload = record("LOCK-0001", "table_cell", factor="F7")
    del payload["cited_ok"]
    problem = read_record(payload, where="runs.F7[0]")
    assert isinstance(problem, str)
    assert "cited_ok" in problem


@pytest.mark.parametrize("field", ["question_id", "factor", "category", "route", "correct"])
def test_a_record_missing_any_field_is_refused(field: str) -> None:
    payload = record("LOCK-0001", "table_cell", factor="F7")
    del payload[field]
    assert isinstance(read_record(payload, where="here"), str)


@pytest.mark.parametrize("value", [1, 0, "true", None, "false"])
def test_a_verdict_that_is_not_a_boolean_is_refused(value: object) -> None:
    """``1`` and ``"yes"`` are both truthy, and a grader's ``0`` is not a wrong answer."""
    payload = record("LOCK-0001", "table_cell", factor="F7", correct=value)
    problem = read_record(payload, where="here")
    assert isinstance(problem, str)
    assert "true or false" in problem


def test_a_cited_ok_that_is_not_boolean_or_null_is_refused() -> None:
    payload = record("LOCK-0001", "table_cell", factor="F7", cited_ok=1)
    assert isinstance(read_record(payload, where="here"), str)


def test_an_unknown_category_is_refused() -> None:
    payload = record("LOCK-0001", "table_cell", factor="F7")
    payload["category"] = "vibes"
    problem = read_record(payload, where="here")
    assert isinstance(problem, str)
    assert "vibes" in problem


def test_an_unknown_route_is_refused() -> None:
    payload = record("LOCK-0001", "table_cell", factor="F7", route="guess")
    problem = read_record(payload, where="here")
    assert isinstance(problem, str)
    assert "six routes" in problem


@pytest.mark.parametrize("value", ["", 7])
def test_an_identifier_that_is_not_a_non_empty_string_is_refused(value: object) -> None:
    """An empty question_id would make every such record look like the same record."""
    payload = record("LOCK-0001", "table_cell", factor="F7")
    payload["question_id"] = value
    problem = read_record(payload, where="here")
    assert isinstance(problem, str)
    assert "non-empty string" in problem


def test_a_probe_smuggled_into_a_factor_run_is_refused() -> None:
    """Reaching G8's probes through a graded rung would change that rung's denominator."""
    raw = artifacts()
    raw["F7"] = [{**probe("PROBE-0001"), "category": "probe"}, *raw["F7"][1:]]
    problem = only(check(raw=raw), "runs.F7/PROBE-0001")
    assert "eight question types" in problem.detail


def test_a_factor_block_without_any_categories_fails() -> None:
    payload = summary()
    del payload["factors"]["F7"]["by_category"]
    problem = only(check(payload), "factors.F7.by_category")
    assert problem.kind == "missing_summary_field"
    assert "G2" in problem.detail


def test_an_unanswerable_block_with_neither_count_is_reported_as_absent() -> None:
    payload = summary()
    payload["unanswerable"] = {"refusal_precision": {"n": 1, "refused": 1}}
    problem = only(check(payload), "unanswerable.over_answered")
    assert problem.kind == "missing_summary_field"


def test_a_record_that_is_not_an_object_is_refused() -> None:
    assert isinstance(read_record(["LOCK-0001"], where="here"), str)


def test_a_malformed_record_is_reported_rather_than_raised() -> None:
    raw = artifacts()
    raw["F7"] = [{"question_id": "LOCK-0001"}, *raw["F7"][1:]]
    found = check(raw=raw)
    assert any(problem.kind == "malformed" for problem in found)


# ------------------------------------------------------------------ resources


def test_a_rounded_resource_number_is_refused() -> None:
    """22.04 rounded to 22.0 turns a failed G10 into a passed one."""
    payload = summary()
    payload["resources"]["vram_peak_gb"] = 22.0
    measured = {**RESOURCES, "vram_peak_gb": 22.04}
    problem = only(check(payload, resources=measured), "resources.vram_peak_gb")
    assert problem.kind == "mismatch"
    assert problem.claimed == "22"
    assert problem.recomputed == "22.04"


def test_a_resource_number_that_is_not_a_number_is_refused() -> None:
    payload = summary()
    payload["resources"]["vram_peak_gb"] = "20.5"
    assert only(check(payload), "resources.vram_peak_gb").kind == "missing_summary_field"


# --------------------------------------------------------------- the G9 boolean itself


def test_a_summary_asserting_g9_while_a_figure_is_wrong_is_itself_a_problem() -> None:
    payload = summary(checks={"data_reproducible": True, "results_reproducible": True})
    payload["route_accuracy"] = {"n": 4, "correct": 3}
    problem = only(check(payload), "checks.results_reproducible")
    assert problem.claimed == "true"
    assert problem.recomputed == "false"


def test_the_g9_claim_stands_when_every_figure_reproduces() -> None:
    payload = summary(checks={"data_reproducible": True, "results_reproducible": True})
    assert check(payload) == ()


# ------------------------------------------------------------------- the disk edge


def write_runs(root: Path, raw: Mapping[str, Sequence[Mapping[str, Any]]]) -> None:
    for name, records in raw.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / RECORDS_FILENAME).write_text(
            "".join(json.dumps(rec, ensure_ascii=False) + "\n" for rec in records),
            encoding="utf-8",
        )


def test_records_written_to_disk_verify_through_the_documented_layout(tmp_path: Path) -> None:
    """The contract run_eval must satisfy, exercised end to end rather than described."""
    write_runs(tmp_path, artifacts())
    (tmp_path / RESOURCES_FILENAME).write_text(json.dumps(dict(RESOURCES)), encoding="utf-8")
    loaded = load_artifacts(tmp_path)
    assert loaded.resources == dict(RESOURCES)
    assert (
        verify(
            summary(),
            loaded.runs,
            expected_lock_sha256=LOCK,
            resources=loaded.resources,
            factors=FACTORS,
        )
        == ()
    )


def test_a_missing_run_directory_is_absent_rather_than_empty(tmp_path: Path) -> None:
    """Loading says what is there; only verify() decides what that means."""
    loaded = load_artifacts(tmp_path)
    assert loaded.runs == {}
    assert loaded.resources is None


def test_blank_lines_in_a_records_file_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "F7").mkdir()
    body = json.dumps(record("LOCK-0001", "table_cell", factor="F7"))
    (tmp_path / "F7" / RECORDS_FILENAME).write_text(f"\n{body}\n\n", encoding="utf-8")
    assert len(load_artifacts(tmp_path).runs["F7"]) == 1


def test_a_records_file_that_cannot_be_parsed_is_an_error_not_an_empty_run(tmp_path: Path) -> None:
    """Reading a broken file as empty would report "nothing was graded" for a stray comma."""
    (tmp_path / "F7").mkdir()
    (tmp_path / "F7" / RECORDS_FILENAME).write_text('{"question_id": ', encoding="utf-8")
    with pytest.raises(ResultIntegrityError, match="not valid JSON"):
        load_artifacts(tmp_path)


def test_a_records_line_holding_something_other_than_an_object_is_refused(tmp_path: Path) -> None:
    (tmp_path / "F7").mkdir()
    (tmp_path / "F7" / RECORDS_FILENAME).write_text("[1, 2, 3]\n", encoding="utf-8")
    with pytest.raises(ResultIntegrityError, match="JSON object"):
        load_artifacts(tmp_path)


def test_malformed_resources_json_is_refused(tmp_path: Path) -> None:
    (tmp_path / RESOURCES_FILENAME).write_text("{oops}", encoding="utf-8")
    with pytest.raises(ResultIntegrityError, match="not valid JSON"):
        load_artifacts(tmp_path)


def test_resources_json_holding_a_list_is_refused(tmp_path: Path) -> None:
    (tmp_path / RESOURCES_FILENAME).write_text("[]", encoding="utf-8")
    with pytest.raises(ResultIntegrityError, match="JSON object"):
        load_artifacts(tmp_path)


# ----------------------------------------------------------------------- reporting


def test_a_problem_names_the_field_and_both_sides() -> None:
    payload = summary()
    payload["route_accuracy"] = {"n": 4, "correct": 2}
    text = str(only(check(payload), "route_accuracy"))
    assert "route_accuracy" in text
    assert "2/4" in text
    assert "4/4" in text


def test_a_problem_about_the_artifacts_reads_without_a_summary_side() -> None:
    raw = artifacts()
    raw["F7"] = [*raw["F7"], record("LOCK-0001", "table_cell", factor="F7")]
    text = str(only(check(raw=raw), "runs.F7/LOCK-0001"))
    assert "summary says" not in text


def test_a_problem_serialises_for_the_verification_artifact() -> None:
    payload = summary(protocol_lock_sha256="c" * 64)
    as_json = only(check(payload), "protocol_lock_sha256").to_json()
    assert as_json["kind"] == "lock_mismatch"
    assert set(as_json) == {"field", "kind", "claimed", "recomputed", "detail"}
