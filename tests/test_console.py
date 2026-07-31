"""Script output must not die on a character the console cannot encode.

The failure this prevents was silent in the worst way: listing the audit sample hit a ≤ in
a question, raised UnicodeEncodeError under cp950, and stopped -- so the output looked like
a shorter sample rather than a crashed one.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from twfi.console import use_utf8_output


class FakeStream:
    """A stream that records what it was reconfigured to."""

    def __init__(self) -> None:
        self.encoding: str | None = None
        self.errors: str | None = None

    def reconfigure(self, *, encoding: str, errors: str) -> None:
        self.encoding = encoding
        self.errors = errors


class RefusingStream(FakeStream):
    def reconfigure(self, *, encoding: str, errors: str) -> None:
        raise ValueError("underlying stream has been detached")


class PlainStream:
    """A stream with no reconfigure at all, as a captured or wrapped stream may be."""


def test_it_switches_both_streams_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    out, err = FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    assert use_utf8_output() == ("stdout", "stderr")
    assert (out.encoding, err.encoding) == ("utf-8", "utf-8")


def test_unencodable_characters_degrade_rather_than_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """errors="replace" is the point: losing one glyph beats losing the rest of the report."""
    out = FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", FakeStream())

    use_utf8_output()
    assert out.errors == "replace"


def test_a_stream_that_cannot_be_reconfigured_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirected or wrapped stream is normal, not an error."""
    monkeypatch.setattr(sys, "stdout", PlainStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())

    assert use_utf8_output() == ("stderr",)


def test_a_stream_that_refuses_is_reported_by_omission(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", RefusingStream())
    monkeypatch.setattr(sys, "stderr", FakeStream())

    assert use_utf8_output() == ("stderr",)


def test_a_missing_stream_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """sys.stdout can be absent entirely in a windowed interpreter."""
    monkeypatch.delattr(sys, "stdout", raising=False)
    monkeypatch.setattr(sys, "stderr", FakeStream())

    assert use_utf8_output() == ("stderr",)


def test_calling_it_twice_is_harmless(monkeypatch: pytest.MonkeyPatch) -> None:
    out: Any = FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", FakeStream())

    use_utf8_output()
    assert use_utf8_output() == ("stdout", "stderr")
    assert out.encoding == "utf-8"
