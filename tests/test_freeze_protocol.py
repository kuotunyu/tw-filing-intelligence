"""The freeze command must not make a draft label permanent."""

from scripts.freeze_protocol import _protocol_status_problem


def test_a_draft_protocol_cannot_be_frozen() -> None:
    problem = _protocol_status_problem("`status: DRAFT — not frozen`\n")

    assert problem is not None
    assert "DRAFT" in problem


def test_a_protocol_without_an_explicit_status_cannot_be_frozen() -> None:
    assert _protocol_status_problem("# protocol\n") is not None


def test_an_explicitly_final_protocol_is_freezeable() -> None:
    assert _protocol_status_problem("`status: FINAL — lock file proves freeze`\n") is None
