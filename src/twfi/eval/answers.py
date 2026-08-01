"""Protocol 3.1 normalization and 3.3 answer scoring.

Deterministic on purpose. The brief is explicit that anything scoreable without a judge model
must be scored without one, and every metric here is: exact match, character-bigram F1, a numeric
comparison against a declared tolerance, unit and period agreement, and refusal.

Three decisions in protocol 3.1 are easy to get backwards, so each is a named function with a
test rather than a line inside a bigger one:

* **`12.3%` and `0.123` are not equivalent.** The gold ``unit`` decides which is meant. Treating
  them as equal would silently pass an answer that is off by a factor of a hundred, and ratio
  questions are a whole hard category.
* **Parenthesised negatives are negative.** `(1,234)` is -1234 in every filing in this corpus.
  Reading it as positive flips the sign of a real figure.
* **民國 and 西元 years convert.** `112年` and `2023` are the same period, and a period metric
  that called them different would fail every correct answer written in the other convention.

Unit and period are scored *separately* from the figure, because protocol 3.3 says so and
because the failure they catch is specific: a system that returns the right digits under the
wrong unit is not right, and collapsing the three into one number hides which of them broke.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from twfi.eval.gold import GoldRecord
from twfi.numeric.amounts import canonical_unit, parse_amount

__all__ = [
    "CJK_UNIT_SCALES",
    "normalise_text",
    "roc_to_common_era",
    "exact_match",
    "token_f1",
    "numeric_match",
    "unit_match",
    "period_match",
    "is_refusal",
    "score_answer",
    "AnswerScore",
]

#: Protocol 3.1 rule 2. Expanded when a bare figure carries one of these, so 「1.2億」 and
#: 「120,000,000」 compare equal. The unit *field* is judged separately by :func:`unit_match`;
#: this is only about the magnitude written inside the answer string.
CJK_UNIT_SCALES: dict[str, Decimal] = {
    "億": Decimal(100_000_000),
    "萬": Decimal(10_000),
    "千": Decimal(1_000),
}

#: 「民國112年」 or 「112年」. Two or three digits, because the ROC calendar is at 114 and a
#: four-digit year is already Common Era.
#:
#: The lookbehind is load-bearing: without it the pattern matched 「23年」 inside 「2023年」 and
#: rewrote a Common Era year to 3934.
_ROC_YEAR = re.compile(r"(?<!\d)(?:民國)?(?P<year>\d{2,3})\s*年")

#: Currency spellings protocol 3.1 rule 5 folds to TWD.
_CURRENCY = re.compile(r"新台幣|新臺幣|NT\$|NTD|TWD|\$")


def normalise_text(text: str) -> str:
    """Protocol 3.1's text-level rules, applied before any string comparison.

    NFKC here rather than the NFC used in :mod:`twfi.parsing.normalise`, and the difference is
    deliberate. The parser must not rewrite 「（一）」 to 「(一)」 because other modules match that
    punctuation literally against the page. An *answer* has no such contract -- it is being
    compared to another answer -- and fullwidth-to-halfwidth folding is exactly what rule 1 asks
    for.
    """
    folded = unicodedata.normalize("NFKC", text)
    folded = _CURRENCY.sub("TWD", folded)
    folded = folded.replace(",", "")
    return "".join(folded.split()).casefold()


def roc_to_common_era(text: str) -> str:
    """Rewrite every ROC year in a string to its Common Era equivalent.

    Applied to both sides before comparison, so 「112年度」 and 「2023年度」 agree without either
    being privileged. A four-digit year is left alone: it is already CE, and adding 1911 to it
    would invent a year.
    """
    return _ROC_YEAR.sub(lambda m: f"{int(m.group('year')) + 1911}年", text)


def _magnitude(text: str, *, unit: str | None = None) -> Decimal | None:
    """The number an answer states, with a CJK scale word applied. ``None`` if it states none.

    ``unit`` is the gold record's declared unit, and passing it is what stops a *unit* being read
    as a *multiplier*. Protocol 3.1 rule 2 expands 億 and 萬 and 千元, then adds in parentheses
    「保留原單位欄位另判」 -- the unit field is judged separately -- and that parenthetical is the
    whole of this distinction:

        「1.2億」        with unit 元    -> 120,000,000   (億 is a magnitude word)
        「530,738,356 千元」 with unit 千元 -> 530,738,356   (千元 is the unit; gold says so too)

    Both are "number, scale word, 元". Only the gold unit says which is which, and reading the
    second as a multiplier makes a correct answer wrong by a factor of a thousand.
    """
    flat = normalise_text(text)
    declared = normalise_text(unit) if unit else ""
    if declared and flat.endswith(declared):
        # The answer restated the unit gold already declares, so the figure is in that unit
        # rather than being scaled by it.
        flat = flat[: -len(declared)]
    else:
        for word, scale in CJK_UNIT_SCALES.items():
            match = re.search(rf"(-?\d+(?:\.\d+)?)\s*{word}", flat)
            if match:
                return Decimal(match.group(1)) * scale
    return parse_amount(flat)


def exact_match(predicted: str, record: GoldRecord) -> bool:
    """Normalised string equality against the gold answer or any acceptable variant."""
    if record.answer is None:
        return False
    candidates = (record.answer, *record.acceptable_variants)
    got = roc_to_common_era(normalise_text(predicted))
    return any(got == roc_to_common_era(normalise_text(item)) for item in candidates)


def _bigrams(text: str) -> list[str]:
    """Character bigrams. Protocol 3.3: Chinese F1 is computed at character-bigram level.

    Whitespace-free single tokens for text shorter than two characters, so 「元」 contributes
    itself rather than nothing.
    """
    flat = normalise_text(text)
    if len(flat) < 2:
        return [flat] if flat else []
    return [flat[index : index + 2] for index in range(len(flat) - 1)]


def token_f1(predicted: str, gold: str) -> float:
    """Character-bigram F1, with multiplicity.

    Multiset rather than set intersection: an answer that repeats a phrase five times should not
    score as though it said it once, and a set-based F1 cannot tell those apart.
    """
    got, want = _bigrams(predicted), _bigrams(gold)
    if not got or not want:
        return 1.0 if got == want else 0.0
    overlap = 0
    remaining = list(want)
    for gram in got:
        if gram in remaining:
            remaining.remove(gram)
            overlap += 1
    if overlap == 0:
        return 0.0
    precision = overlap / len(got)
    recall = overlap / len(want)
    return 2 * precision * recall / (precision + recall)


def numeric_match(predicted: str, record: GoldRecord) -> bool | None:
    """Whether a figure falls within the declared tolerance. ``None`` if this is not numeric.

    Protocol 3.1 rule 7: numeric questions compare as numbers, never as strings, so
    「530,738,356」 and 「530738356」 agree and 「530,738,357」 does not.
    """
    if record.answer is None:
        return None
    expected = _magnitude(record.answer, unit=record.unit)
    if expected is None:
        return None
    got = _magnitude(predicted, unit=record.unit)
    if got is None:
        return False
    tolerance = record.tolerance
    if tolerance is None:
        return got == expected
    if tolerance.type == "absolute":
        return abs(got - expected) <= Decimal(str(tolerance.value))
    #: Relative to the gold value. A gold of zero admits only an exact zero -- any relative
    #: window around zero has zero width, and widening it would be inventing a tolerance the
    #: annotator did not declare.
    if expected == 0:
        return got == 0
    return abs(got - expected) / abs(expected) <= Decimal(str(tolerance.value))


def unit_match(predicted_unit: str | None, record: GoldRecord) -> bool | None:
    """Whether the stated unit is the gold unit. ``None`` when gold declares none."""
    if record.unit is None:
        return None
    return canonical_unit(predicted_unit) == canonical_unit(record.unit)


def period_match(predicted_period: str | None, record: GoldRecord) -> bool:
    """Whether the stated period is the gold period, ROC and CE treated as one.

    Always applicable, unlike unit: ``GoldRecord.period`` is required, so every question has a
    period to be right or wrong about. An answer that states none is wrong rather than exempt.
    """
    if predicted_period is None:
        return False
    return roc_to_common_era(normalise_text(predicted_period)) == roc_to_common_era(
        normalise_text(record.period)
    )


#: Phrases a refusal uses. Matched on the normalised answer, so spacing and fullwidth forms do
#: not matter. Deliberately short and explicit rather than a model judging "did it refuse":
#: refusal precision and recall are a gate (G7, G8), and a judged gate is not deterministic.
_REFUSAL_MARKERS: tuple[str, ...] = (
    "無法回答",
    "無法從",
    "文件中沒有",
    "文件未",
    "查無",
    "找不到",
    "資料不足",
    "不足以回答",
    "cannot answer",
    "not stated",
    "does not state",
    "no such",
    "notfound",
    "insufficient",
)


def is_refusal(predicted: str) -> bool:
    """Whether an answer declines rather than asserts."""
    flat = normalise_text(predicted)
    return any(marker in flat for marker in (normalise_text(m) for m in _REFUSAL_MARKERS))


class AnswerScore:
    """Every protocol 3.3 judgement for one answer, kept apart rather than combined.

    ``None`` means "not applicable to this question", which is different from ``False``. A
    narrative question has no numeric verdict; scoring it as a numeric failure would drag the
    numeric metric down with questions that never tested it.
    """

    __slots__ = ("exact", "f1", "numeric", "period", "refused", "should_refuse", "unit")

    def __init__(
        self,
        *,
        exact: bool,
        f1: float,
        numeric: bool | None,
        unit: bool | None,
        period: bool,
        refused: bool,
        should_refuse: bool,
    ) -> None:
        self.exact = exact
        self.f1 = f1
        self.numeric = numeric
        self.unit = unit
        self.period = period
        self.refused = refused
        self.should_refuse = should_refuse

    @property
    def correct(self) -> bool:
        """The primary answer metric: right refusal, or a figure/text that matches.

        A numeric question is judged on its number -- exact string match on a figure is too
        brittle to be the primary metric when 「530,738,356 千元」 and 「530738356」 are the same
        answer -- and everything else on exact match.
        """
        if self.should_refuse:
            return self.refused
        if self.refused:
            return False
        return self.numeric if self.numeric is not None else self.exact

    def to_json(self) -> dict[str, object]:
        return {
            "exact_match": self.exact,
            "token_f1": round(self.f1, 4),
            "numeric_ok": self.numeric,
            "unit_ok": self.unit,
            "period_ok": self.period,
            "refused": self.refused,
            "should_refuse": self.should_refuse,
            "correct": self.correct,
        }


def score_answer(
    predicted: str,
    record: GoldRecord,
    *,
    predicted_unit: str | None = None,
    predicted_period: str | None = None,
) -> AnswerScore:
    """Score one answer against one gold record on every protocol 3.3 metric."""
    refused = is_refusal(predicted)
    should_refuse = not record.answerable
    return AnswerScore(
        exact=exact_match(predicted, record),
        f1=token_f1(predicted, record.answer or ""),
        numeric=numeric_match(predicted, record),
        unit=unit_match(predicted_unit, record),
        period=period_match(predicted_period, record),
        refused=refused,
        should_refuse=should_refuse,
    )


def refusal_rates(scores: Sequence[AnswerScore]) -> dict[str, float | int]:
    """Refusal precision and recall, with ``answerable=false`` as the positive class.

    Both, not one: a system that refuses everything has perfect recall, and a system that never
    refuses has undefined precision and perfect accuracy on the answerable majority. G7 and G8
    exist because either failure alone looks fine on the other number.
    """
    refused = [score for score in scores if score.refused]
    should = [score for score in scores if score.should_refuse]
    correct = [score for score in refused if score.should_refuse]
    return {
        "refused": len(refused),
        "should_refuse": len(should),
        "correct_refusals": len(correct),
        "precision": round(len(correct) / len(refused), 4) if refused else 0.0,
        "recall": round(len(correct) / len(should), 4) if should else 0.0,
    }
