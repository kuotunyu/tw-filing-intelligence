"""Evaluation harness: gold schema, metrics, runner, reporting, and the gate.

Nothing in here may be changed after ``scripts/freeze_protocol.py`` has run
without bumping ``protocol_version`` and re-running the whole locked evaluation.
"""

from __future__ import annotations

__all__: list[str] = []
