"""The error taxonomy is part of the protocol: failures must be bucketable."""

from __future__ import annotations

import pytest

from twfi import errors


def test_all_exported_names_exist() -> None:
    for name in errors.__all__:
        assert hasattr(errors, name), name


@pytest.mark.parametrize("name", errors.__all__)
def test_every_error_derives_from_base(name: str) -> None:
    cls = getattr(errors, name)
    assert issubclass(cls, errors.TwfiError)
    assert issubclass(cls, Exception)


@pytest.mark.parametrize(
    ("child", "parent"),
    [
        (errors.DisallowedHostError, errors.DataAccessError),
        (errors.DownloadLimitExceededError, errors.DataAccessError),
        (errors.RateLimitViolationError, errors.DataAccessError),
        (errors.ManifestError, errors.DataAccessError),
        (errors.HashMismatchError, errors.DataAccessError),
        (errors.UnitMismatchError, errors.NumericRouteError),
        (errors.TemplateMissError, errors.NumericRouteError),
        (errors.CitationInvalidError, errors.EvidenceError),
        (errors.GoldSchemaError, errors.EvaluationError),
        (errors.LeakageError, errors.EvaluationError),
        (errors.ProtocolLockError, errors.EvaluationError),
        (errors.ResultIntegrityError, errors.EvaluationError),
    ],
)
def test_hierarchy_edges(child: type[Exception], parent: type[Exception]) -> None:
    assert issubclass(child, parent)


def test_errors_carry_messages() -> None:
    err = errors.DisallowedHostError("example.com is not allowlisted")
    assert "example.com" in str(err)
