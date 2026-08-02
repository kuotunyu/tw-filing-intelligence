"""Company names in a question define the filing search space."""

from __future__ import annotations

import pytest

from twfi.index.scope import company_document_scope


@pytest.mark.parametrize(
    ("query", "expected_prefix"),
    [
        ("台塑民國112年度的資產總計是多少？", "1301-"),
        ("中華電信 112 年的總資產", "2412-"),
        ("台積公司 2024 年營收", "2330-"),
        ("鴻海精密 113 年財報", "2317-"),
        ("國泰金 2024 年淨利", "2882-"),
        ("請查 2330 的年報", "2330-"),
    ],
)
def test_known_company_names_and_codes_select_only_that_company(
    query: str, expected_prefix: str
) -> None:
    scope = company_document_scope(query)

    assert scope
    assert all(doc_id.startswith(expected_prefix) for doc_id in scope)


def test_scope_contains_every_usable_filing_for_cross_document_questions() -> None:
    assert company_document_scope("比較台積電兩年度") == frozenset(
        {"2330-FY2023-AR", "2330-FY2024-AR", "2330-FY2024-FS"}
    )


def test_scope_excludes_declared_but_unusable_filings() -> None:
    assert company_document_scope("鴻海 2024 年") == frozenset({"2317-FY2024-FS"})


def test_query_without_a_registered_company_keeps_the_corpus_unscoped() -> None:
    assert company_document_scope("今年的資產總計是多少？") is None
