"""Which model-drafted gold records a person has to check, and why those ones.

Gold may be drafted by a model reading rendered page images (D-019). The risk that
introduces is not bad transcription -- a machine is better at digits than a person, as
PROBE-0004 showed -- but bad *question selection*: a drafter that also chooses the
questions can drift toward what the pipeline handles well, and nothing in the record would
reveal it.

The audit sample is the defence, so the drafter must not be able to choose it. Two rules
do that:

* **Seeded.** The draw uses the protocol's decoding seed, so the same set and size always
  yield the same records and anyone can re-derive which ones should have been checked.
  Presenting a hand-picked "sample" is therefore visible.
* **Some types are never sampled.** A chart answer is the one kind with no text-layer
  corroboration available at all: ``verify_gold_answers`` can confirm the values sit inside
  the cited crop, but not that 6% belongs to 民國113年 rather than 112年. Sampling those at
  a 2-in-3 rate would leave the highest-circularity category unchecked a third of the time.

This lives in the package rather than in the script because it is protocol, not plumbing:
the sampling rule is part of what the study claims about its own gold, so it is imported,
tested, and covered like everything else that the report leans on.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Sequence

from twfi.eval.gold import GoldRecord

__all__ = ["ALWAYS_AUDITED", "AUDIT_SEED", "DEFAULT_SAMPLE", "audit_sample", "eligible_records"]

#: The protocol's decoding seed (2.5), reused so the sample is fixed by the protocol rather
#: than by whoever runs the script.
AUDIT_SEED = 20260731

#: Protocol 1.5 as amended by D-019. Eight of the locked set is roughly a quarter, which is
#: enough that a systematic drafting bias would show up in more than one record.
DEFAULT_SAMPLE = 8

#: Question types where every drafted record is audited rather than sampled (D-020).
ALWAYS_AUDITED: frozenset[str] = frozenset({"chart_value_trend"})


def eligible_records(records: Iterable[GoldRecord]) -> list[GoldRecord]:
    """The records an audit can apply to, in id order.

    Anything a person did not both choose and answer is eligible: the audit exists to check
    question selection, so a human-read figure under a machine-chosen question still needs
    looking at.

    Sorted by id, not left in file order, because the drafter controls the file order and
    must not be able to shift the draw by moving lines around.
    """
    return sorted(
        (record for record in records if not record.is_fully_human),
        key=lambda record: record.question_id,
    )


def audit_sample(records: Sequence[GoldRecord], *, size: int = DEFAULT_SAMPLE) -> list[GoldRecord]:
    """Every always-audited record, plus a reproducible sample of the rest, in id order."""
    drafted = eligible_records(records)
    # Forced records are kept out of the draw rather than added on top of it, so that
    # introducing a forced type does not redraw the sample for every other type. The
    # records already checked before chart questions existed are still the ones the seed
    # picks, which means adding a category costs the auditor that category and nothing else.
    forced = [record for record in drafted if record.question_type in ALWAYS_AUDITED]
    pool = [record for record in drafted if record.question_type not in ALWAYS_AUDITED]
    if len(pool) <= size:
        return _by_id(forced + pool)
    # Reproducibility is the requirement here, not unpredictability. A cryptographic source
    # would make the sample unauditable: nobody could re-derive which records should have
    # been checked, which is the whole point of seeding it.
    chosen = random.Random(AUDIT_SEED).sample(range(len(pool)), size)  # noqa: S311
    return _by_id(forced + [pool[index] for index in sorted(chosen)])


def _by_id(records: Iterable[GoldRecord]) -> list[GoldRecord]:
    return sorted(records, key=lambda record: record.question_id)
