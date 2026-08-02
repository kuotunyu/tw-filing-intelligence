"""Recompute every number in ``summary.json`` from the raw per-question records.

Gate G9 is the one gate that is about the other gates: it asks whether the summary they are
judged on is a *report* of what happened or a *claim* about it. Nothing else in the harness
would notice the difference. ``scripts/run_gate.py`` reads ``summary.json`` and believes it,
so a category rate typed in by hand, computed over the wrong denominator, or left behind by
an earlier run would travel all the way to a GO. This module recomputes each proportion from
the graded records under ``results/runs/**`` and names every disagreement, which is what
gives the script wrapping it the right to set ``summary["checks"]["results_reproducible"]``
-- the boolean G9 reads.

Five properties, in descending order of how easily they could have gone the other way:

* **Absent evidence fails.** A summary field with no raw records behind it is a failure,
  never a pass. :mod:`twfi.eval.gates` makes the same choice for the same reason: if a
  missing input verified for want of a counterexample, deleting the records would be the
  cheapest way to make a summary reproducible.
* **A silent summary is not a clean summary.** An empty summary checked against an empty
  artifact set yields one problem per protocol-required field, not "all fine".
  Reproducibility is a claim about numbers that exist, so nothing is not everything.
* **A rate without its denominator is refused**, via :func:`twfi.eval.gates.read_proportion`
  -- the gates' own parser, so a payload that satisfies G9 cannot then be unreadable to G4.
* **The raw records are checked against the protocol, not trusted.** A record whose
  ``gold_route`` disagrees with protocol 3.5's mapping, or whose ``question_id`` appears
  twice in one run, would let a recomputation agree with the summary while both were wrong.
* **Ladders are compared item by item.** G2 and G3 subtract one factor's rate from
  another's. If two runs were graded on different question sets the difference is not a
  gain, and no individual proportion looks wrong -- so the item sets are compared directly.

What this module does *not* do is grade anything. ``correct``, ``refused`` and ``cited_ok``
arrive already graded by the run; re-deriving them here would create a second grader whose
disagreements with the first would be invisible. The record contract is on :class:`RawRecord`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, get_args

from twfi.errors import ResultIntegrityError
from twfi.eval.gates import Proportion, read_proportion
from twfi.protocol import (
    BASELINE_FACTOR,
    CANDIDATE_FACTOR,
    FACTOR_IDS,
    ROUTE_BY_QUESTION_TYPE,
    QuestionType,
    Route,
)

__all__ = [
    "ProblemKind",
    "Problem",
    "RawRecord",
    "Artifacts",
    "PROBE_RUN",
    "PROBE_CATEGORY",
    "RECORDS_FILENAME",
    "RESOURCES_FILENAME",
    "RESOURCE_KEYS",
    "NUMERIC_ROUTE_CATEGORIES",
    "REQUIRED_RECORD_FIELDS",
    "read_record",
    "verify",
    "load_artifacts",
]

#: One run directory per factor, plus one for the no-evidence probes, each holding
#: ``records.jsonl``. The probes are a separate run because G8 clears their retrieval: folded
#: into the candidate's records they would quietly move overall accuracy's denominator from
#: 33 items to 38, and every rate in the summary with it.
PROBE_RUN: Final = "probes"

#: The ``category`` a probe record carries. Deliberately not one of the eight question types:
#: a probe is not an ``unanswerable`` gold item (protocol 4 / G8 -- a good probe is one the
#: model probably *does* know the answer to, asked with no evidence in front of it), and
#: giving it a question type would enrol it in a gold category's numerator.
PROBE_CATEGORY: Final = "probe"

RECORDS_FILENAME: Final = "records.jsonl"
RESOURCES_FILENAME: Final = "resources.json"

#: Gate G5's scope, in protocol 4's words: the answerable ``numeric_calculation`` +
#: ``cross_period_comparison`` + ``table_cell`` items that the numeric route actually
#: handled. Belongs in :mod:`twfi.protocol` beside the other gate constants; it lives here
#: only because that module is frozen shut for the moment.
NUMERIC_ROUTE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {"numeric_calculation", "cross_period_comparison", "table_cell"}
)

#: Gate G10's three measurements. Unlike everything else in the summary these are not
#: proportions and cannot be recomputed from per-question records, so the run has to write
#: them down under the same names -- see :func:`load_artifacts`.
RESOURCE_KEYS: Final[tuple[str, ...]] = ("retrieval_p95_s", "generation_p95_s", "vram_peak_gb")

#: Every field a graded record must carry. ``cited_ok`` is on this list even though it may be
#: ``null``: see :class:`RawRecord`.
REQUIRED_RECORD_FIELDS: Final[tuple[str, ...]] = (
    "question_id",
    "factor",
    "category",
    "answerable",
    "gold_route",
    "route",
    "handled_route",
    "correct",
    "refused",
    "cited_ok",
)

_BOOLEAN_FIELDS: Final[tuple[str, ...]] = ("answerable", "correct", "refused")
_TEXT_FIELDS: Final[tuple[str, ...]] = (
    "question_id",
    "factor",
    "category",
    "gold_route",
    "route",
    "handled_route",
)
_QUESTION_TYPES: Final[frozenset[str]] = frozenset(get_args(QuestionType))
_ROUTES: Final[frozenset[str]] = frozenset(get_args(Route))

ProblemKind = Literal[
    "missing_summary_field",
    "missing_artifacts",
    "malformed",
    "mismatch",
    "lock_mismatch",
    "inconsistent_artifacts",
]


@dataclass(frozen=True, slots=True)
class Problem:
    """One disagreement, in enough detail to act on without re-running anything.

    ``field`` is the dotted path into ``summary.json`` -- or the artifact's location, when the
    problem is in the records rather than in the summary. Both sides of a mismatch are always
    carried: "citation_validity does not reproduce" is not actionable, whereas "the summary
    says 32/33 and the records give 29/33" says which of the two to go and look at.
    """

    field: str
    kind: ProblemKind
    claimed: str
    recomputed: str
    detail: str = ""

    def __str__(self) -> str:
        if not self.claimed and not self.recomputed:
            return f"{self.field}: {self.detail}"
        line = f"{self.field}: summary says {self.claimed}; raw artifacts give {self.recomputed}"
        return f"{line} -- {self.detail}" if self.detail else line

    def to_json(self) -> dict[str, str]:
        return {
            "field": self.field,
            "kind": self.kind,
            "claimed": self.claimed,
            "recomputed": self.recomputed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class RawRecord:
    """One graded question from one run: the unit ``run_eval`` writes and this module reads.

    The contract is ``results/runs/<run>/records.jsonl``, one JSON object per line, where
    ``<run>`` is a factor id (``F0``..``F7``) or ``probes``. Extra keys -- latencies, token
    counts, the model's actual answer, retrieval traces -- are allowed and ignored here; the
    fields below are the ones any summary number depends on, and **all of them are
    required**:

    ``question_id``
        The gold record's id. Unique within a run: a duplicate line inflates a numerator and
        its denominator together, which no single rate reveals.
    ``factor``
        The ladder rung this grading belongs to, and it must agree with the directory the
        record was found in. A record filed under the wrong run moves evidence between the
        two factors whose difference G2 and G3 are computed from.
    ``category``
        The gold ``question_type``, or ``"probe"`` in the probes run.
    ``answerable``
        The gold record's flag. Must be false exactly for the ``unanswerable`` category (and
        for every probe), which is what keeps an unanswerable item out of G5's denominator.
    ``gold_route``
        The route protocol 3.5 assigns to ``category``. Written down and then checked against
        the protocol rather than taken on trust -- a run that recorded its own answer here
        would score its route accuracy against itself.
    ``route``
        The pipeline's effective final label, one of the six in :data:`twfi.protocol.Route`.
        A refusal is labelled ``unanswerable`` so G6 measures the route the pipeline ended on.
    ``handled_route``
        The answer backend that handled the selected path before the effective refusal label.
        This differs from ``route`` when, for example, the numeric backend refuses: G5 must
        count that attempted numeric item as an incorrect numeric result instead of removing
        it from the denominator merely because its final label is ``unanswerable``.
    ``correct``
        The graded verdict under protocol 4's overall-accuracy definition: numeric tolerance
        for numeric items, exact match or token-F1 >= 0.8 for prose, and *correct refusal*
        for unanswerable ones. One field, because protocol 4 weights all items equally.
    ``refused``
        Whether the system declined to answer. On an unanswerable item this must agree with
        ``correct``; protocol 3.3 defines over-answering as the complement of refusal, so a
        record claiming a correct answer it did not refuse would make G7 and overall accuracy
        disagree about the same item with nothing to show for it.
    ``cited_ok``
        Whether the citation resolved to real evidence containing the answer span or the
        operands (protocol 3.4). May be ``null`` where citation validity does not apply --
        but may not be *omitted*, because an omitted key is indistinguishable from a
        forgotten one and G4's denominator is exactly the set of records that declared
        themselves in scope.
    """

    question_id: str
    factor: str
    category: str
    answerable: bool
    gold_route: str
    route: str
    handled_route: str
    correct: bool
    refused: bool
    cited_ok: bool | None


@dataclass(frozen=True, slots=True)
class Artifacts:
    """What ``results/runs/**`` contributes to verification, as read off the disk.

    ``runs`` maps a run id to the still-unparsed record payloads; :func:`verify` parses them,
    so a malformed record becomes a reported problem rather than a load-time exception.
    """

    runs: Mapping[str, tuple[Mapping[str, Any], ...]]
    resources: Mapping[str, Any] | None = None


def read_record(payload: Any, *, where: str) -> RawRecord | str:
    """Parse one record, or say what is wrong with it.

    Returns the problem as a string rather than raising, for the reason
    :func:`twfi.eval.gates.read_proportion` does: one bad line must make verification *fail
    with a reason*, not abort it and leave the summary unjudged.
    """
    if not isinstance(payload, Mapping):
        return f"{where} must be a JSON object, got {type(payload).__name__}"
    missing = [name for name in REQUIRED_RECORD_FIELDS if name not in payload]
    if missing:
        return (
            f"{where} is missing {', '.join(missing)}; every field is required, and cited_ok "
            "must be present even when it is null"
        )
    for name in _TEXT_FIELDS:
        value = payload[name]
        if not isinstance(value, str) or not value:
            return f"{where}.{name} must be a non-empty string, got {value!r}"
    for name in _BOOLEAN_FIELDS:
        # Not a truthiness test: 1 and "yes" would both become True, and a grader that wrote
        # 0 for "not graded yet" would be read as a wrong answer.
        if not isinstance(payload[name], bool):
            return f"{where}.{name} must be true or false, got {payload[name]!r}"
    cited_ok = payload["cited_ok"]
    if cited_ok is not None and not isinstance(cited_ok, bool):
        return f"{where}.cited_ok must be true, false, or null, got {cited_ok!r}"
    if payload["category"] not in _QUESTION_TYPES | {PROBE_CATEGORY}:
        return (
            f"{where}.category is {payload['category']!r}, which is neither one of the eight "
            f"question types nor {PROBE_CATEGORY!r}"
        )
    for name in ("route", "gold_route", "handled_route"):
        if payload[name] not in _ROUTES:
            return f"{where}.{name} is not one of the six routes: {payload[name]!r}"
    return RawRecord(
        question_id=str(payload["question_id"]),
        factor=str(payload["factor"]),
        category=str(payload["category"]),
        answerable=bool(payload["answerable"]),
        gold_route=str(payload["gold_route"]),
        route=str(payload["route"]),
        handled_route=str(payload["handled_route"]),
        correct=bool(payload["correct"]),
        refused=bool(payload["refused"]),
        cited_ok=None if cited_ok is None else bool(cited_ok),
    )


# --------------------------------------------------------------- recomputation


def _accuracy(records: Sequence[RawRecord], *, category: str | None = None) -> Proportion | None:
    """Correct over graded, optionally within one category. ``None`` when nothing is in scope.

    ``None`` rather than 0/0: a proportion over no records is not zero, it is absent, and the
    caller must fail rather than compare against it.
    """
    kept = [r for r in records if category is None or r.category == category]
    if not kept:
        return None
    return Proportion(sum(1 for r in kept if r.correct), len(kept))


def _citation_validity(records: Sequence[RawRecord]) -> Proportion | None:
    """Protocol 3.4. The denominator is the records that declared citation applicable."""
    kept = [r for r in records if r.cited_ok is not None]
    if not kept:
        return None
    return Proportion(sum(1 for r in kept if r.cited_ok), len(kept))


def _numeric_route_accuracy(records: Sequence[RawRecord]) -> Proportion | None:
    """Gate G5: answerable numeric-ish items that the numeric route actually handled.

    Conditioning on ``route == "numeric"`` is protocol 4's wording, and it cuts both ways --
    a router that sends every hard numeric item elsewhere shrinks this denominator instead of
    lowering the rate. G6 is what catches that, which is why both gates exist.
    """
    kept = [
        r
        for r in records
        if r.category in NUMERIC_ROUTE_CATEGORIES and r.answerable and r.handled_route == "numeric"
    ]
    if not kept:
        return None
    return Proportion(sum(1 for r in kept if r.correct), len(kept))


def _route_accuracy(records: Sequence[RawRecord]) -> Proportion | None:
    """Gate G6, over every graded item: the chosen route equals the gold route."""
    if not records:
        return None
    return Proportion(sum(1 for r in records if r.route == r.gold_route), len(records))


def _over_answered(records: Sequence[RawRecord]) -> Proportion | None:
    """Gate G7. Protocol 3.3 defines over-answering as the complement of refusal."""
    kept = [r for r in records if r.category == "unanswerable"]
    if not kept:
        return None
    return Proportion(sum(1 for r in kept if not r.refused), len(kept))


def _refusal_precision(records: Sequence[RawRecord]) -> Proportion | None:
    """Gate G7's second condition: correct refusals over all refusals.

    ``None`` when the system refused nothing at all. That is not precision 1.0 -- a summary
    reporting a precision on an empty denominator is reporting a number about no events.
    """
    kept = [r for r in records if r.refused]
    if not kept:
        return None
    return Proportion(sum(1 for r in kept if not r.answerable), len(kept))


def _probe_refusals(records: Sequence[RawRecord]) -> Proportion | None:
    """Gate G8: how many of the no-evidence probes were refused."""
    if not records:
        return None
    return Proportion(sum(1 for r in records if r.refused), len(records))


# ------------------------------------------------------------------- comparison


def _check_proportion(
    field: str,
    claimed: Any,
    recomputed: Proportion | None,
    *,
    absent_artifacts: str,
) -> Problem | None:
    """Compare one summary proportion with its recomputation, in that order of suspicion."""
    evidence = str(recomputed) if recomputed is not None else "nothing"
    parsed = read_proportion(claimed, where=field)
    if isinstance(parsed, str):
        kind: ProblemKind = "missing_summary_field" if claimed is None else "malformed"
        return Problem(
            field, kind, "nothing" if claimed is None else repr(claimed), evidence, parsed
        )
    if recomputed is None:
        return Problem(field, "missing_artifacts", str(parsed), evidence, absent_artifacts)
    if (parsed.numerator, parsed.denominator) != (recomputed.numerator, recomputed.denominator):
        return Problem(
            field,
            "mismatch",
            str(parsed),
            str(recomputed),
            "the summary figure is not what the graded records add up to",
        )
    assert isinstance(claimed, Mapping)  # read_proportion accepted it above
    expected_rate = round(recomputed.rate, 6)
    claimed_rate = _finite_float(claimed.get("rate"))
    if claimed_rate is None:
        kind = "missing_summary_field" if "rate" not in claimed else "malformed"
        return Problem(
            f"{field}.rate",
            kind,
            "nothing" if "rate" not in claimed else repr(claimed.get("rate")),
            f"{expected_rate:g}",
            "the registered summary schema requires the recomputed rate",
        )
    if claimed_rate != expected_rate:
        return Problem(
            f"{field}.rate",
            "mismatch",
            f"{claimed_rate:g}",
            f"{expected_rate:g}",
            "the reported rate does not agree with its own verified counts",
        )
    expected_ci = [round(bound, 6) for bound in recomputed.interval()]
    claimed_ci = claimed.get("ci95")
    if "ci95" not in claimed:
        return Problem(
            f"{field}.ci95",
            "missing_summary_field",
            "nothing",
            repr(expected_ci),
            "the registered summary schema requires a Wilson 95% confidence interval",
        )
    if (
        not isinstance(claimed_ci, list)
        or len(claimed_ci) != 2
        or any(_finite_float(bound) is None for bound in claimed_ci)
    ):
        return Problem(
            f"{field}.ci95",
            "malformed",
            repr(claimed_ci),
            repr(expected_ci),
            "ci95 must be a two-number array containing finite Wilson interval bounds",
        )
    normalised_ci = [float(bound) for bound in claimed_ci]
    if normalised_ci != expected_ci:
        return Problem(
            f"{field}.ci95",
            "mismatch",
            repr(claimed_ci),
            repr(expected_ci),
            "the reported interval is not the Wilson interval for the verified counts",
        )
    return None


def _record_problems(record: RawRecord, run: str, candidate: str) -> list[str]:
    """Contradictions between a record and the protocol, or between a record and its run."""
    problems: list[str] = []
    if run == PROBE_RUN:
        if record.category != PROBE_CATEGORY:
            problems.append(
                f"category is {record.category!r}; records in the {PROBE_RUN} run must be "
                f"{PROBE_CATEGORY!r} so a probe cannot be counted into a gold category"
            )
        if record.answerable:
            problems.append(
                "answerable=True, but a no-evidence probe has no evidence to answer from"
            )
        if record.factor != candidate:
            problems.append(
                f"factor is {record.factor!r}; G8 probes the candidate system, so it must be "
                f"{candidate!r}"
            )
        return problems
    if record.factor != run:
        problems.append(
            f"factor is {record.factor!r} but the record was found in run {run!r}; a record "
            "filed under the wrong factor moves evidence between the two runs G2 compares"
        )
    if record.category not in _QUESTION_TYPES:
        problems.append(f"category {record.category!r} is not one of the eight question types")
        return problems
    expected_route = ROUTE_BY_QUESTION_TYPE[record.category]
    if record.gold_route != expected_route:
        problems.append(
            f"gold_route is {record.gold_route!r}, but protocol 3.5 maps {record.category} to "
            f"{expected_route!r}; the gold route is not the run's to choose"
        )
    if record.answerable != (record.category != "unanswerable"):
        problems.append(
            f"answerable={record.answerable} contradicts category {record.category!r}; the "
            "unanswerable category is answerable=False and no other category is"
        )
    if record.category == "unanswerable" and record.correct != record.refused:
        problems.append(
            f"correct={record.correct} and refused={record.refused} disagree on an "
            "unanswerable item, where protocol 4 grades correct as 'refused correctly'"
        )
    return problems


def _parse_runs(
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    runs: Sequence[str],
    candidate: str,
) -> tuple[dict[str, tuple[RawRecord, ...]], list[Problem]]:
    """Parse and sanity-check every run's records. A missing run parses to no records."""
    parsed: dict[str, tuple[RawRecord, ...]] = {}
    problems: list[Problem] = []
    for run in runs:
        payloads = raw.get(run, ())
        records: list[RawRecord] = []
        seen: dict[str, int] = {}
        for index, payload in enumerate(payloads):
            where = f"runs.{run}[{index}]"
            record = read_record(payload, where=where)
            if isinstance(record, str):
                problems.append(Problem(where, "malformed", "", "", record))
                continue
            if record.question_id in seen:
                problems.append(
                    Problem(
                        f"runs.{run}/{record.question_id}",
                        "inconsistent_artifacts",
                        "",
                        "",
                        f"graded twice (records {seen[record.question_id]} and {index}); a "
                        "duplicate inflates a numerator and its denominator together",
                    )
                )
                continue
            seen[record.question_id] = index
            problems.extend(
                Problem(f"runs.{run}/{record.question_id}", "inconsistent_artifacts", "", "", note)
                for note in _record_problems(record, run, candidate)
            )
            records.append(record)
        parsed[run] = tuple(records)
    return parsed, problems


def _lock_problems(summary: Mapping[str, Any], expected: str | None) -> list[Problem]:
    """G9's second half: the summary must name the frozen protocol it was produced under."""
    claimed = summary.get("protocol_lock_sha256")
    shown = str(claimed) if isinstance(claimed, str) and claimed else "nothing"
    if expected is None:
        return [
            Problem(
                "protocol_lock_sha256",
                "missing_artifacts",
                shown,
                "no protocol lock to compare against",
                "G9 requires the lock hash to match; either the protocol was never frozen or "
                "the lock file was not supplied, and an unmatched hash is not a match",
            )
        ]
    if shown == "nothing":
        return [
            Problem(
                "protocol_lock_sha256",
                "missing_summary_field",
                shown,
                expected,
                "the summary does not record which frozen protocol produced it",
            )
        ]
    if shown != expected:
        return [
            Problem(
                "protocol_lock_sha256",
                "lock_mismatch",
                shown,
                expected,
                "the summary was produced under a different protocol lock than the one on "
                "disk; one of the two is not the locked run",
            )
        ]
    return []


def _ladder_problems(baseline: str, candidate: str) -> list[Problem]:
    """Protocol 2 fixes which rung is the baseline and which is the candidate."""
    problems: list[Problem] = []
    for field, declared, expected in (
        ("baseline", baseline, BASELINE_FACTOR),
        ("candidate", candidate, CANDIDATE_FACTOR),
    ):
        if declared != expected:
            problems.append(
                Problem(
                    field,
                    "mismatch",
                    declared,
                    expected,
                    "the factor ladder is pre-registered; relabelling which run is the "
                    "candidate would change every gain the gates compute",
                )
            )
    return problems


def _factor_problems(
    summary: Mapping[str, Any],
    records: Mapping[str, tuple[RawRecord, ...]],
    factors: Sequence[str],
) -> list[Problem]:
    """Every rung's overall accuracy and every category rate under it."""
    graded = sum(len(run) for run in records.values())
    block = summary.get("factors")
    if not isinstance(block, Mapping):
        return [
            Problem(
                "factors",
                "missing_summary_field",
                "nothing",
                f"{graded} graded records across {len(records)} runs",
                "the whole factor ladder is absent from the summary",
            )
        ]
    problems: list[Problem] = []
    for factor in factors:
        run = records.get(factor, ())
        entry = block.get(factor)
        if not isinstance(entry, Mapping):
            problems.append(
                Problem(
                    f"factors.{factor}",
                    "missing_summary_field",
                    "nothing",
                    f"{len(run)} graded records" if run else "no graded records",
                    "protocol 5 step 8 runs F0..F7 on the locked set and forbids re-running "
                    "only the favourable configurations, so a subset is not a locked run",
                )
            )
            continue
        problem = _check_proportion(
            f"factors.{factor}.overall_accuracy",
            entry.get("overall_accuracy"),
            _accuracy(run),
            absent_artifacts=f"results/runs/{factor}/{RECORDS_FILENAME} graded nothing",
        )
        if problem is not None:
            problems.append(problem)
        problems.extend(_category_problems(factor, entry, run))
    return problems


def _category_problems(
    factor: str, entry: Mapping[str, Any], run: Sequence[RawRecord]
) -> list[Problem]:
    """Per-category rates, over the union of what is claimed and what was graded.

    The union, not the summary's keys: a category present in the records and absent from the
    summary is as much a reproducibility failure as the other way round, and it is the
    direction that produces a summary which looks complete.
    """
    by_category = entry.get("by_category")
    if not isinstance(by_category, Mapping):
        return [
            Problem(
                f"factors.{factor}.by_category",
                "missing_summary_field",
                "nothing",
                f"{len({r.category for r in run})} categories graded",
                "protocol 4's small-sample section requires every category to report n and a "
                "numerator, and G2 is judged category by category",
            )
        ]
    problems: list[Problem] = []
    for category in sorted(set(by_category) | {r.category for r in run}):
        problem = _check_proportion(
            f"factors.{factor}.by_category.{category}",
            by_category.get(category),
            _accuracy(run, category=category),
            absent_artifacts=f"no record in run {factor} has category {category!r}",
        )
        if problem is not None:
            problems.append(problem)
    return problems


def _candidate_problems(summary: Mapping[str, Any], records: Sequence[RawRecord]) -> list[Problem]:
    """The gate metrics the summary reports for the candidate alone: G4, G5, G6, G7."""
    problems: list[Problem] = []
    for field, recomputed, absent in (
        (
            "citation_validity",
            _citation_validity(records),
            "no graded record declares cited_ok, so G4 has no denominator",
        ),
        (
            "numeric_route_accuracy",
            _numeric_route_accuracy(records),
            "no answerable numeric-category item was handled by the numeric route",
        ),
        ("route_accuracy", _route_accuracy(records), "the candidate run graded nothing"),
    ):
        problem = _check_proportion(field, summary.get(field), recomputed, absent_artifacts=absent)
        if problem is not None:
            problems.append(problem)
    problems.extend(_unanswerable_problems(summary, records))
    return problems


def _unanswerable_problems(
    summary: Mapping[str, Any], records: Sequence[RawRecord]
) -> list[Problem]:
    """G7's two numbers, read exactly the way :mod:`twfi.eval.gates` reads them."""
    block = summary.get("unanswerable")
    if not isinstance(block, Mapping):
        return [
            Problem(
                "unanswerable",
                "missing_summary_field",
                "nothing",
                f"{sum(1 for r in records if r.category == 'unanswerable')} unanswerable items "
                "graded",
                "G7 is judged on this block",
            )
        ]
    # gates.py assembles the over-answer proportion out of two sibling keys; assembling it
    # the same way here is what keeps a payload that satisfies G9 readable to G7.
    claimed: Any = {"n": block.get("n"), "correct": block.get("over_answered")}
    for key in ("rate", "ci95"):
        if key in block:
            claimed[key] = block[key]
    if claimed["n"] is None and claimed["correct"] is None:
        claimed = None
    problems: list[Problem] = []
    for field, payload, recomputed, absent in (
        (
            "unanswerable.over_answered",
            claimed,
            _over_answered(records),
            "no graded record has category 'unanswerable'",
        ),
        (
            "unanswerable.refusal_precision",
            block.get("refusal_precision"),
            _refusal_precision(records),
            "the candidate refused nothing, so refusal precision has no denominator",
        ),
    ):
        problem = _check_proportion(field, payload, recomputed, absent_artifacts=absent)
        if problem is not None:
            problems.append(problem)
    return problems


def _resource_problems(
    summary: Mapping[str, Any], resources: Mapping[str, Any] | None
) -> list[Problem]:
    """G10's three measurements, compared exactly.

    Exactly, and not within a tolerance: rounding a 22.04 GB peak to 22.0 turns a failed gate
    into a passed one, and there is no tolerance that admits that while remaining honest.
    These are the only summary numbers not recomputable from per-question records, which is
    why the run has to write them down separately rather than being taken at its word.
    """
    block = summary.get("resources")
    measured_note = "nothing" if resources is None else f"{RESOURCES_FILENAME} has no such key"
    if not isinstance(block, Mapping):
        return [
            Problem(
                "resources",
                "missing_summary_field",
                "nothing",
                "nothing" if resources is None else f"{len(resources)} measurements on disk",
                "G10 is soft, but an unreported resource number is not a passed G10",
            )
        ]
    problems: list[Problem] = []
    for key in RESOURCE_KEYS:
        field = f"resources.{key}"
        claimed = _as_float(block.get(key))
        measured = _as_float(resources.get(key)) if resources is not None else None
        if claimed is None:
            problems.append(
                Problem(
                    field,
                    "missing_summary_field",
                    "nothing" if block.get(key) is None else repr(block.get(key)),
                    measured_note if measured is None else f"{measured:g}",
                    "G10 is judged on this number",
                )
            )
        elif measured is None:
            problems.append(
                Problem(field, "missing_artifacts", f"{claimed:g}", measured_note, "unmeasured")
            )
        elif claimed != measured:
            problems.append(
                Problem(
                    field,
                    "mismatch",
                    f"{claimed:g}",
                    f"{measured:g}",
                    "the reported figure is not the measured one",
                )
            )
    return problems


def _as_float(value: Any) -> float | None:
    """A number, or ``None``. ``True`` is not 1.0 here, and neither is ``"22"``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _finite_float(value: Any) -> float | None:
    """A finite JSON number; booleans and NaN/Infinity are not reported measurements."""
    parsed = _as_float(value)
    if parsed is None or not math.isfinite(parsed):
        return None
    return parsed


def _coverage_problems(
    records: Mapping[str, tuple[RawRecord, ...]], candidate: str, factors: Sequence[str]
) -> list[Problem]:
    """Every rung must be graded on the same items as the candidate.

    G2 and G3 subtract one rung's rate from another's. If the two were graded on different
    question sets the difference is not a gain -- and every individual proportion still
    reproduces, so nothing else here would notice.
    """
    reference = records.get(candidate, ())
    if not reference:
        return []
    expected = {r.question_id for r in reference}
    problems: list[Problem] = []
    for factor in factors:
        if factor == candidate:
            continue
        run = records.get(factor, ())
        if not run:
            continue  # already reported as missing artifacts by _factor_problems
        seen = {r.question_id for r in run}
        notes = [
            f"{label} {_name_sample(sorted(ids))}"
            for label, ids in (("missing", expected - seen), ("extra", seen - expected))
            if ids
        ]
        if notes:
            problems.append(
                Problem(
                    f"runs.{factor}",
                    "inconsistent_artifacts",
                    "",
                    "",
                    f"graded on a different item set than {candidate}: {'; '.join(notes)}. A "
                    "gain between two different question sets is not a gain",
                )
            )
    return problems


def _name_sample(ids: Sequence[str], limit: int = 3) -> str:
    head = ", ".join(ids[:limit])
    return head if len(ids) <= limit else f"{head} (+{len(ids) - limit} more)"


def _claim_problems(summary: Mapping[str, Any], found: Sequence[Problem]) -> list[Problem]:
    """The summary must not already assert the boolean this verification decides.

    Checked last, because it is the only check whose answer depends on the other answers.
    """
    checks = summary.get("checks")
    claimed = checks.get("results_reproducible") if isinstance(checks, Mapping) else None
    if claimed is not True or not found:
        return []
    return [
        Problem(
            "checks.results_reproducible",
            "mismatch",
            "true",
            "false",
            f"the summary already asserts G9 while {len(found)} figure(s) do not reproduce; "
            "the flag belongs to this verification, not to the run that wrote the summary",
        )
    ]


def verify(
    summary: Mapping[str, Any],
    raw: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    expected_lock_sha256: str | None = None,
    resources: Mapping[str, Any] | None = None,
    factors: Sequence[str] = FACTOR_IDS,
) -> tuple[Problem, ...]:
    """Return every reason ``summary`` is not reproducible from ``raw``. Empty means it is.

    Args:
        summary: The parsed ``summary.json``.
        raw: Run id (a factor id, or ``probes``) to that run's record payloads, exactly as
            they came off disk. Payloads are parsed here so a malformed record is a reported
            problem rather than an exception thrown at load time.
        expected_lock_sha256: The digest of the protocol lock on disk. ``None`` is itself a
            problem: G9 requires the hash to match, and there is nothing to match against.
        resources: The run's own record of G10's three measurements. See
            :func:`_resource_problems` for why these cannot be recomputed.
        factors: Which rungs the summary must report. Defaults to the pre-registered ladder;
            injectable so that the checks themselves are testable on a smaller ladder rather
            than only ever seen passing on the real one.

    The parameters carry no defaults that could make verification easier. In particular an
    absent run, an absent lock and an absent measurement all produce problems, because the
    one thing this function must never do is confirm a number nothing supports.
    """
    baseline = str(summary.get("baseline", BASELINE_FACTOR))
    candidate = str(summary.get("candidate", CANDIDATE_FACTOR))
    records, problems = _parse_runs(raw, [*factors, PROBE_RUN], candidate)
    problems.extend(_lock_problems(summary, expected_lock_sha256))
    problems.extend(_ladder_problems(baseline, candidate))
    problems.extend(_factor_problems(summary, records, factors))
    problems.extend(_candidate_problems(summary, records.get(candidate, ())))
    problem = _check_proportion(
        "probes",
        summary.get("probes"),
        _probe_refusals(records.get(PROBE_RUN, ())),
        absent_artifacts=f"results/runs/{PROBE_RUN}/{RECORDS_FILENAME} graded nothing; G8 is "
        "judged on the hand-built no-evidence probes and on nothing else",
    )
    if problem is not None:
        problems.append(problem)
    problems.extend(_resource_problems(summary, resources))
    problems.extend(_coverage_problems(records, candidate, factors))
    problems.extend(_claim_problems(summary, problems))
    return tuple(problems)


# ------------------------------------------------------------------ the disk edge


def load_artifacts(runs_dir: Path, *, runs: Iterable[str] = (*FACTOR_IDS, PROBE_RUN)) -> Artifacts:
    """Read every ``<run>/records.jsonl`` and ``resources.json`` under ``runs_dir``.

    The only function here that touches a filesystem; everything that decides anything is
    pure and above it.

    A missing file is simply absent from the result rather than an error -- whether a summary
    figure may stand without artifacts is :func:`verify`'s judgement, and it always answers
    no. A file that *exists* and cannot be parsed is an error, because that is a broken
    artifact rather than missing evidence: treating it as empty would report the loudest
    possible failure (nothing was graded) for the quietest possible cause (a stray comma).

    Raises:
        ResultIntegrityError: If a records file or ``resources.json`` exists and is not valid
            JSON in the documented shape.
    """
    collected: dict[str, tuple[Mapping[str, Any], ...]] = {}
    for run in runs:
        path = runs_dir / run / RECORDS_FILENAME
        if path.is_file():
            collected[run] = _read_jsonl(path)
    resources_path = runs_dir / RESOURCES_FILENAME
    return Artifacts(
        runs=collected,
        resources=_read_json_object(resources_path) if resources_path.is_file() else None,
    )


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    payloads: list[Mapping[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ResultIntegrityError(f"{path}:{number} is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ResultIntegrityError(
                f"{path}:{number} must hold a JSON object, got {type(payload).__name__}"
            )
        payloads.append(payload)
    return tuple(payloads)


def _read_json_object(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ResultIntegrityError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ResultIntegrityError(f"{path} must hold a JSON object, got {type(payload).__name__}")
    return payload
