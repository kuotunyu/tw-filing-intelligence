"""The deterministic numeric route.

Reliable structured figures do not go through an embedding model to be guessed at
(DECISIONS D-005). They go into DuckDB with their unit, currency, basis and source,
and are answered by parameterised SQL templates that report the formula and every
operand they used.

The LLM never writes SQL here. A question the templates do not cover is a
:class:`~twfi.errors.TemplateMissError` -- an honest capability limit that error
analysis can count -- rather than a free-form query whose failure mode nobody can
reproduce.
"""

from __future__ import annotations

__all__: list[str] = []
