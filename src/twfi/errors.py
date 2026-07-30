"""Exception hierarchy for the feasibility harness.

Every failure mode that the protocol treats as *meaningful* gets its own type, so
that error analysis can bucket failures deterministically instead of parsing
free-text messages.
"""

from __future__ import annotations

__all__ = [
    "TwfiError",
    "ConfigError",
    "RepoLayoutError",
    # io
    "DataAccessError",
    "DisallowedHostError",
    "DownloadLimitExceededError",
    "RateLimitViolationError",
    "ManifestError",
    "HashMismatchError",
    # parsing / numeric
    "ParsingError",
    "NumericRouteError",
    "UnitMismatchError",
    "TemplateMissError",
    # answering
    "EvidenceError",
    "CitationInvalidError",
    "ContractViolationError",
    # evaluation
    "EvaluationError",
    "GoldSchemaError",
    "LeakageError",
    "ProtocolLockError",
    "ResultIntegrityError",
]


class TwfiError(Exception):
    """Base class for all errors raised by this package."""


class ConfigError(TwfiError):
    """Invalid or missing configuration."""


class RepoLayoutError(TwfiError):
    """The repository layout is not what the harness expects."""


# --------------------------------------------------------------------- io


class DataAccessError(TwfiError):
    """Base class for data acquisition failures."""


class DisallowedHostError(DataAccessError):
    """A request target is not on the hard-coded host allowlist (SSRF guard)."""


class DownloadLimitExceededError(DataAccessError):
    """A single file or a whole run exceeded its download budget."""


class RateLimitViolationError(DataAccessError):
    """The client attempted to exceed the politeness budget for a host."""


class ManifestError(DataAccessError):
    """A manifest is malformed or violates an invariant."""


class HashMismatchError(DataAccessError):
    """A local file's SHA-256 does not match the manifest."""


# ---------------------------------------------------------- parsing/numeric


class ParsingError(TwfiError):
    """A document could not be parsed into the expected structure."""


class NumericRouteError(TwfiError):
    """The deterministic numeric route could not produce a trustworthy answer."""


class UnitMismatchError(NumericRouteError):
    """Operands disagree on unit, currency, or statement basis."""


class TemplateMissError(NumericRouteError):
    """No SQL template covers the requested computation (an honest capability limit)."""


# ---------------------------------------------------------------- answering


class EvidenceError(TwfiError):
    """Evidence is missing, insufficient, or not traceable to a source."""


class CitationInvalidError(EvidenceError):
    """A citation cannot be resolved to an existing page, table cell, crop, or SQL row."""


class ContractViolationError(TwfiError):
    """A model response violated the shared answer/citation contract."""


# --------------------------------------------------------------- evaluation


class EvaluationError(TwfiError):
    """Base class for evaluation-time failures."""


class GoldSchemaError(EvaluationError):
    """A gold record does not satisfy the protocol's required schema."""


class LeakageError(EvaluationError):
    """Development and locked splits overlap, or gold answers are machine-generated."""


class ProtocolLockError(EvaluationError):
    """The frozen protocol, gold set, manifests, or model pins have changed."""


class ResultIntegrityError(EvaluationError):
    """summary.json cannot be reconstructed from the raw run artifacts."""
