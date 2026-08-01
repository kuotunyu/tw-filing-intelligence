"""Turn ``summary.json`` into a GO / CONDITIONAL_GO / NO_GO decision, mechanically.

Protocol 4. The thresholds live in :class:`twfi.protocol.Gates` and are frozen before the
locked run; this module only applies them. Nobody may overwrite the verdict by hand, which
is why the decision is a pure function of the summary and why it is tested rather than
narrated.

Three properties matter more than the arithmetic:

* **Absent evidence fails.** A gate whose input is missing is *not* passed for want of a
  counterexample. Every gate that cannot be computed is reported as failing, with the
  missing key named. The opposite convention would make deleting a metric the cheapest way
  to reach GO.
* **A rate without its denominator is refused.** Protocol 4 already says a percentage
  reported without ``n`` makes the report incomplete; here that is enforced rather than
  requested. ``{"rate": 0.95}`` is rejected the same way a missing key is, because 0.95 of
  two items and 0.95 of forty are not the same evidence and the gate cannot tell them apart.
* **Soft and hard failures are different.** G10 is resources; failing it alone yields
  CONDITIONAL_GO. Any hard failure is NO_GO, and no amount of soft success offsets it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal

from twfi.protocol import GATES, HARD_CATEGORIES, SINGLE_GATE_CATEGORIES, Gates

__all__ = [
    "Decision",
    "GateOutcome",
    "Proportion",
    "wilson_interval",
    "mcnemar_exact",
    "read_proportion",
    "evaluate",
    "decide",
    "SOFT_GATES",
]

Decision = Literal["GO", "CONDITIONAL_GO", "NO_GO"]

#: Protocol 4: only G10 is soft. Failing it alone downgrades GO to CONDITIONAL_GO.
SOFT_GATES: Final[frozenset[str]] = frozenset({"G10"})

#: Wilson score interval at 95%, two-sided.
_Z: Final = 1.959963984540054


@dataclass(frozen=True, slots=True)
class Proportion:
    """A rate that knows its own denominator.

    The study's categories hold three to six items, so a rate without ``n`` cannot be
    interpreted at all -- one item is 17 to 33 percentage points. Carrying the denominator
    is what lets :func:`wilson_interval` report how little the number constrains.
    """

    numerator: int
    denominator: int

    def __post_init__(self) -> None:
        if self.denominator <= 0:
            raise ValueError("a proportion needs a positive denominator")
        if not 0 <= self.numerator <= self.denominator:
            raise ValueError(f"numerator {self.numerator} outside 0..{self.denominator}")

    @property
    def rate(self) -> float:
        return self.numerator / self.denominator

    @property
    def percentage_points(self) -> float:
        return self.rate * 100.0

    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.numerator, self.denominator)

    def __str__(self) -> str:
        low, high = self.interval()
        return f"{self.rate:.1%} ({self.numerator}/{self.denominator}, 95% CI {low:.1%}-{high:.1%})"


def wilson_interval(successes: int, trials: int) -> tuple[float, float]:
    """Wilson score interval at 95%.

    Not the normal approximation: at these sample sizes it produces bounds outside [0, 1]
    and collapses to zero width at 0 or 100 percent, which would understate uncertainty in
    exactly the cases this study is most likely to hit.
    """
    if trials <= 0:
        raise ValueError("an interval needs at least one trial")
    phat = successes / trials
    denominator = 1 + _Z**2 / trials
    centre = phat + _Z**2 / (2 * trials)
    spread = _Z * math.sqrt(phat * (1 - phat) / trials + _Z**2 / (4 * trials**2))
    return (
        max(0.0, (centre - spread) / denominator),
        min(1.0, (centre + spread) / denominator),
    )


def mcnemar_exact(left: Mapping[str, int], right: Mapping[str, int]) -> tuple[int, int, float]:
    """Exact McNemar on paired per-question outcomes. Returns ``(left-only, right-only, p)``.

    Paired because the two configurations answer *the same* questions: comparing two independent
    binomial proportions throws away that pairing and is the wrong test. Exact rather than the
    chi-square approximation because the discordant counts here are single digits, where the
    approximation is not usable.

    The two-sided p is the binomial probability, under the null that a discordant pair falls
    either way with probability one half, of a split at least as lopsided as the observed one.
    """
    shared = sorted(set(left) & set(right))
    only_left = sum(1 for qid in shared if left[qid] and not right[qid])
    only_right = sum(1 for qid in shared if right[qid] and not left[qid])
    discordant = only_left + only_right
    if discordant == 0:
        # No question distinguishes them at all, so the data cannot separate them: p = 1.
        return 0, 0, 1.0
    smaller = min(only_left, only_right)
    tail = sum(math.comb(discordant, index) for index in range(smaller + 1))
    probability = min(1.0, 2.0 * tail / (2**discordant))
    return only_left, only_right, probability


def read_proportion(payload: Any, *, where: str) -> Proportion | str:
    """Parse ``{"n": …, "correct": …}`` into a :class:`Proportion`, or say what is wrong.

    Returns the problem as a string rather than raising, because a malformed summary must
    make its gate *fail with a reason* rather than crash the run and leave no verdict.
    """
    if payload is None:
        return f"{where} is missing"
    if not isinstance(payload, Mapping):
        return f"{where} must be an object with n and a count, got {type(payload).__name__}"
    denominator = payload.get("n")
    numerator = next(
        (payload[key] for key in ("correct", "valid", "refused", "passed") if key in payload),
        None,
    )
    if denominator is None or numerator is None:
        # The one thing this must never do is fall back to a bare "rate".
        if "rate" in payload:
            return (
                f"{where} reports a rate without n and a count; protocol 4 treats a "
                "percentage without its denominator as incomplete"
            )
        return f"{where} needs n and one of correct/valid/refused/passed"
    try:
        return Proportion(int(numerator), int(denominator))
    except (TypeError, ValueError) as exc:
        return f"{where} is not a usable proportion: {exc}"


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One gate's verdict, with enough detail to be argued with."""

    gate: str
    name: str
    passed: bool
    detail: str
    kind: Literal["hard", "soft"] = "hard"
    observed: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "name": self.name,
            "kind": self.kind,
            "passed": self.passed,
            "detail": self.detail,
            "observed": list(self.observed),
        }


def _flag(summary: Mapping[str, Any], key: str) -> bool | str:
    """A boolean another script is responsible for, e.g. manifest verification."""
    checks = summary.get("checks")
    if not isinstance(checks, Mapping) or key not in checks:
        return f"checks.{key} is missing; the script that owns it has not reported"
    value = checks[key]
    if not isinstance(value, bool):
        return f"checks.{key} must be true or false, got {value!r}"
    return value


def _category_gain(summary: Mapping[str, Any], category: str) -> tuple[float, str] | str:
    """Percentage-point gain of the candidate over the baseline in one category."""
    factors = summary.get("factors")
    baseline_id = str(summary.get("baseline", "F0"))
    candidate_id = str(summary.get("candidate", "F7"))
    if not isinstance(factors, Mapping):
        return "factors is missing"
    out: dict[str, Proportion] = {}
    for role, factor in (("baseline", baseline_id), ("candidate", candidate_id)):
        block = factors.get(factor)
        if not isinstance(block, Mapping):
            return f"factors.{factor} is missing"
        by_category = block.get("by_category")
        if not isinstance(by_category, Mapping):
            return f"factors.{factor}.by_category is missing"
        parsed = read_proportion(
            by_category.get(category), where=f"factors.{factor}.by_category.{category}"
        )
        if isinstance(parsed, str):
            return parsed
        out[role] = parsed
    gain = out["candidate"].percentage_points - out["baseline"].percentage_points
    return gain, f"{category}: {baseline_id} {out['baseline']} -> {candidate_id} {out['candidate']}"


def _pooled(summary: Mapping[str, Any], categories: Sequence[str]) -> tuple[float, str] | str:
    """Gain over the union of several categories, pooled by item rather than averaged.

    Averaging category rates would weight a three-item category equally with a five-item
    one. Protocol 4's pooled set is defined as 18 *items*, so the pooling is over items.
    """
    factors = summary.get("factors")
    baseline_id = str(summary.get("baseline", "F0"))
    candidate_id = str(summary.get("candidate", "F7"))
    if not isinstance(factors, Mapping):
        return "factors is missing"
    totals: dict[str, list[int]] = {"baseline": [0, 0], "candidate": [0, 0]}
    for role, factor in (("baseline", baseline_id), ("candidate", candidate_id)):
        block = factors.get(factor)
        if not isinstance(block, Mapping):
            return f"factors.{factor} is missing"
        by_category = block.get("by_category")
        if not isinstance(by_category, Mapping):
            return f"factors.{factor}.by_category is missing"
        for category in categories:
            parsed = read_proportion(
                by_category.get(category), where=f"factors.{factor}.by_category.{category}"
            )
            if isinstance(parsed, str):
                return parsed
            totals[role][0] += parsed.numerator
            totals[role][1] += parsed.denominator
    baseline = Proportion(totals["baseline"][0], totals["baseline"][1])
    candidate = Proportion(totals["candidate"][0], totals["candidate"][1])
    gain = candidate.percentage_points - baseline.percentage_points
    return gain, f"pooled hard set: {baseline_id} {baseline} -> {candidate_id} {candidate}"


def _threshold_gate(
    gate: str,
    name: str,
    parsed: Proportion | str,
    minimum: float,
) -> GateOutcome:
    if isinstance(parsed, str):
        return GateOutcome(gate, name, False, parsed)
    passed = parsed.rate >= minimum
    return GateOutcome(
        gate,
        name,
        passed,
        f"{'meets' if passed else 'below'} the {minimum:.0%} threshold",
        observed=(str(parsed),),
    )


def evaluate(summary: Mapping[str, Any], gates: Gates = GATES) -> tuple[GateOutcome, ...]:
    """Apply every gate to a summary. Order is G1..G10 and does not depend on outcomes."""
    return (
        _g1(summary),
        _g2(summary, gates),
        _g3(summary, gates),
        _threshold_gate(
            "G4",
            "citation validity",
            read_proportion(summary.get("citation_validity"), where="citation_validity"),
            gates.min_citation_validity,
        ),
        _threshold_gate(
            "G5",
            "numeric route accuracy",
            read_proportion(summary.get("numeric_route_accuracy"), where="numeric_route_accuracy"),
            gates.min_numeric_route_accuracy,
        ),
        _threshold_gate(
            "G6",
            "route accuracy",
            read_proportion(summary.get("route_accuracy"), where="route_accuracy"),
            gates.min_route_accuracy,
        ),
        _g7(summary, gates),
        _g8(summary, gates),
        _g9(summary),
        _g10(summary, gates),
    )


def _g1(summary: Mapping[str, Any]) -> GateOutcome:
    flag = _flag(summary, "data_reproducible")
    if isinstance(flag, str):
        return GateOutcome("G1", "data reproducible", False, flag)
    return GateOutcome(
        "G1",
        "data reproducible",
        flag,
        "verify_manifests reported every SHA-256 matching"
        if flag
        else "verify_manifests reported a mismatch or an unreproducible source",
    )


def _g2(summary: Mapping[str, Any], gates: Gates) -> GateOutcome:
    pooled = _pooled(summary, sorted(HARD_CATEGORIES))
    if isinstance(pooled, str):
        return GateOutcome("G2", "hard category gain", False, pooled)
    pooled_gain, pooled_detail = pooled

    singles: list[str] = []
    best: tuple[float, str] | None = None
    for category in sorted(SINGLE_GATE_CATEGORIES):
        result = _category_gain(summary, category)
        if isinstance(result, str):
            return GateOutcome("G2", "hard category gain", False, result)
        gain, detail = result
        singles.append(f"{detail} ({gain:+.1f}pp)")
        if best is None or gain > best[0]:
            best = (gain, category)

    pooled_ok = pooled_gain >= gates.pooled_hard_min_gain_pp
    single_ok = best is not None and best[0] >= gates.single_hard_min_gain_pp
    reasons = []
    if not pooled_ok:
        reasons.append(
            f"pooled gain {pooled_gain:+.1f}pp is below {gates.pooled_hard_min_gain_pp}pp"
        )
    if not single_ok:
        reasons.append(
            f"best single category gain {best[0]:+.1f}pp ({best[1]}) is below "
            f"{gates.single_hard_min_gain_pp}pp"
            if best is not None
            else "no single-gate category could be read"
        )
    return GateOutcome(
        "G2",
        "hard category gain",
        pooled_ok and single_ok,
        "; ".join(reasons)
        if reasons
        else (
            f"pooled gain {pooled_gain:+.1f}pp and a single category at "
            f"{best[0]:+.1f}pp ({best[1]}) both clear their thresholds"
            if best is not None
            else "cleared"
        ),
        observed=(f"{pooled_detail} ({pooled_gain:+.1f}pp)", *singles),
    )


def _g3(summary: Mapping[str, Any], gates: Gates) -> GateOutcome:
    factors = summary.get("factors")
    baseline_id = str(summary.get("baseline", "F0"))
    candidate_id = str(summary.get("candidate", "F7"))
    if not isinstance(factors, Mapping):
        return GateOutcome("G3", "no overall regression", False, "factors is missing")
    parsed: dict[str, Proportion] = {}
    for role, factor in (("baseline", baseline_id), ("candidate", candidate_id)):
        block = factors.get(factor)
        overall = block.get("overall_accuracy") if isinstance(block, Mapping) else None
        result = read_proportion(overall, where=f"factors.{factor}.overall_accuracy")
        if isinstance(result, str):
            return GateOutcome("G3", "no overall regression", False, result)
        parsed[role] = result
    drop = parsed["baseline"].percentage_points - parsed["candidate"].percentage_points
    passed = drop <= gates.max_overall_regression_pp
    return GateOutcome(
        "G3",
        "no overall regression",
        passed,
        f"{'within' if passed else 'exceeds'} the {gates.max_overall_regression_pp}pp allowance "
        f"(change {-drop:+.1f}pp)",
        observed=(
            f"{baseline_id} {parsed['baseline']}",
            f"{candidate_id} {parsed['candidate']}",
        ),
    )


def _g7(summary: Mapping[str, Any], gates: Gates) -> GateOutcome:
    block = summary.get("unanswerable")
    if not isinstance(block, Mapping):
        return GateOutcome("G7", "does not over-answer", False, "unanswerable is missing")
    over = read_proportion(
        {"n": block.get("n"), "correct": block.get("over_answered")},
        where="unanswerable.over_answered",
    )
    precision = read_proportion(
        block.get("refusal_precision"), where="unanswerable.refusal_precision"
    )
    if isinstance(over, str):
        return GateOutcome("G7", "does not over-answer", False, over)
    if isinstance(precision, str):
        return GateOutcome("G7", "does not over-answer", False, precision)
    reasons = []
    if over.rate > gates.max_over_answer_rate:
        reasons.append(f"over-answer rate {over.rate:.0%} exceeds {gates.max_over_answer_rate:.0%}")
    if precision.rate < gates.min_refusal_precision:
        reasons.append(
            f"refusal precision {precision.rate:.0%} is below {gates.min_refusal_precision:.0%}"
        )
    return GateOutcome(
        "G7",
        "does not over-answer",
        not reasons,
        "; ".join(reasons) if reasons else "both conditions met",
        observed=(f"over-answered {over}", f"refusal precision {precision}"),
    )


def _g8(summary: Mapping[str, Any], gates: Gates) -> GateOutcome:
    parsed = read_proportion(summary.get("probes"), where="probes")
    if isinstance(parsed, str):
        return GateOutcome("G8", "refuses without evidence", False, parsed)
    passed = parsed.numerator >= gates.min_probe_refusals
    return GateOutcome(
        "G8",
        "refuses without evidence",
        passed,
        f"{parsed.numerator} of {parsed.denominator} probes refused; "
        f"{'meets' if passed else 'below'} the required {gates.min_probe_refusals}",
        observed=(str(parsed),),
    )


def _g9(summary: Mapping[str, Any]) -> GateOutcome:
    flag = _flag(summary, "results_reproducible")
    if isinstance(flag, str):
        return GateOutcome("G9", "results reproducible", False, flag)
    return GateOutcome(
        "G9",
        "results reproducible",
        flag,
        "verify_results recomputed every summary figure from raw artifacts"
        if flag
        else "verify_results could not reproduce a summary figure, or the lock hash differs",
    )


def _g10(summary: Mapping[str, Any], gates: Gates) -> GateOutcome:
    block = summary.get("resources")
    if not isinstance(block, Mapping):
        return GateOutcome("G10", "resources feasible", False, "resources is missing", kind="soft")
    limits = (
        ("retrieval_p95_s", gates.max_retrieval_p95_s, "retrieval p95"),
        ("generation_p95_s", gates.max_generation_p95_s, "generation p95"),
        ("vram_peak_gb", gates.max_vram_peak_gb, "VRAM peak"),
    )
    observed: list[str] = []
    reasons: list[str] = []
    for key, limit, label in limits:
        value = block.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            reasons.append(f"resources.{key} is missing or not a number")
            continue
        observed.append(f"{label} {float(value):g} (limit {limit:g})")
        if float(value) > limit:
            reasons.append(f"{label} {float(value):g} exceeds {limit:g}")
    return GateOutcome(
        "G10",
        "resources feasible",
        not reasons,
        "; ".join(reasons) if reasons else "within every limit",
        kind="soft",
        observed=tuple(observed),
    )


def decide(outcomes: Sequence[GateOutcome]) -> Decision:
    """Protocol 4's decision rule, with no discretion in it.

    An empty outcome list is NO_GO rather than GO: nothing having been evaluated is not
    the same as everything having passed.
    """
    if not outcomes:
        return "NO_GO"
    hard_failed = [o for o in outcomes if o.kind == "hard" and not o.passed]
    if hard_failed:
        return "NO_GO"
    soft_failed = [o for o in outcomes if o.kind == "soft" and not o.passed]
    return "CONDITIONAL_GO" if soft_failed else "GO"
