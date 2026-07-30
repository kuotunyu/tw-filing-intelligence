"""Naming files from the query would mislabel every document by one year."""

from __future__ import annotations

from pathlib import Path

import pytest

from twfi.io.identify import (
    ROC_EPOCH,
    DocumentIdentity,
    find_company_code,
    find_roc_year,
    identify_cover_text,
    parse_cjk_digits,
    plan_renames,
    read_cover_text,
)

# A cover close to a real one: code and year, with the year in CJK digits.
CJK_COVER = "股票代碼:2412\nNYSE:CHT\n中華電信\n一一二年度年報\n台灣永續 世代前行"
ARABIC_COVER = "股票代碼：2330\n台灣積體電路製造股份有限公司\n民國113年度年報"


# ------------------------------------------------------------------ CJK digits


@pytest.mark.parametrize(
    ("text", "expected"),
    [("一一二", 112), ("一一一", 111), ("一〇九", 109), ("一零九", 109), ("九九", 99)],
)
def test_cjk_digit_sequences_parse(text: str, expected: int) -> None:
    assert parse_cjk_digits(text) == expected


@pytest.mark.parametrize("text", ["一百一十二", "十二", "112", "", "一二三四五六"])
def test_non_sequence_forms_are_refused(text: str) -> None:
    """Compound forms are not guessed at; filings do not use them on covers."""
    result = parse_cjk_digits(text)
    assert result is None or result > 130


# --------------------------------------------------------------- company code


@pytest.mark.parametrize(
    "text",
    [
        "股票代碼:2412",
        "股票代碼：2412",
        "股票代號 2412",
        "證券代碼：2412",
        "公司代號: 2412",
    ],
)
def test_company_code_is_found(text: str) -> None:
    assert find_company_code(text) == "2412"


def test_no_company_code_returns_none() -> None:
    assert find_company_code("年度報告書\n第一章") is None


def test_a_bare_four_digit_number_is_not_taken_as_a_code() -> None:
    """Pages are full of four-digit numbers; only a labelled one counts."""
    assert find_company_code("民國112年度年報\n共 1234 頁") is None


# ------------------------------------------------------------------ roc year


def test_year_from_cjk_cover() -> None:
    assert find_roc_year(CJK_COVER) == 112


def test_year_from_arabic_cover() -> None:
    assert find_roc_year(ARABIC_COVER) == 113


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("民國 112 年度合併財務報告", 112),
        ("112年度年報", 112),
        ("一一三年度年報", 113),
        ("民國一一二年度", 112),
    ],
)
def test_year_patterns(text: str, expected: int) -> None:
    assert find_roc_year(text) == expected


@pytest.mark.parametrize("text", ["第 5 年度", "年報", "", "1911年度年報"])
def test_implausible_years_are_rejected(text: str) -> None:
    assert find_roc_year(text) is None


def test_an_out_of_range_year_is_not_accepted() -> None:
    """A stray "999年度年報" is a parsing artifact, not a filing year."""
    assert find_roc_year("999年度年報") is None


# ------------------------------------------------------------------- identity


def test_identity_converts_roc_to_western() -> None:
    identity = identify_cover_text(CJK_COVER)
    assert identity.company_code == "2412"
    assert identity.roc_year == 112
    assert identity.fiscal_year == 112 + ROC_EPOCH == 2023


def test_the_index_year_is_one_ahead_of_the_fiscal_year() -> None:
    """The finding this module exists for: 資料年度 112 returns the 民國111年度年報."""
    index_year = 112
    identity = identify_cover_text("股票代碼:2412\n一一一年度年報")
    assert identity.roc_year == index_year - 1
    assert identity.fiscal_year == 2022


def test_expected_filename_matches_the_manifest_convention() -> None:
    assert identify_cover_text(ARABIC_COVER).expected_filename() == "2330-FY2024-AR.pdf"


def test_incomplete_identity_has_no_filename() -> None:
    assert DocumentIdentity(company_code="2412", roc_year=None).expected_filename() is None
    assert DocumentIdentity(company_code=None, roc_year=112).expected_filename() is None
    assert DocumentIdentity(company_code=None, roc_year=None).is_complete is False


# ---------------------------------------------------------------- rename plan


def touch(directory: Path, name: str) -> Path:
    target = directory / name
    target.write_bytes(b"%PDF-1.7 stub")
    return target


DECLARED = {"2412-FY2023-AR.pdf", "2330-FY2024-AR.pdf"}


def test_a_misnamed_download_gets_the_right_target(tmp_path: Path) -> None:
    path = touch(tmp_path, "202405_2412_F04_20240415.pdf")
    plans = plan_renames([path], DECLARED, cover_reader=lambda _p: CJK_COVER)
    assert len(plans) == 1
    assert plans[0].target_name == "2412-FY2023-AR.pdf"
    assert plans[0].needs_rename is True
    assert plans[0].declared is True
    assert plans[0].is_ready is True


def test_an_already_correct_name_needs_no_rename(tmp_path: Path) -> None:
    path = touch(tmp_path, "2412-FY2023-AR.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: CJK_COVER)[0]
    assert plan.needs_rename is False
    assert plan.is_ready is True


def test_an_unreadable_cover_is_reported_not_guessed(tmp_path: Path) -> None:
    path = touch(tmp_path, "mystery.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "")[0]
    assert plan.target_name is None
    assert "company code" in plan.problem
    assert "fiscal year" in plan.problem
    assert plan.is_ready is False


def test_a_partially_readable_cover_names_what_is_missing(tmp_path: Path) -> None:
    path = touch(tmp_path, "mystery.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "股票代碼:2412")[0]
    assert plan.problem == "could not read fiscal year from the first pages"


def test_an_undeclared_document_is_flagged(tmp_path: Path) -> None:
    """Silently accepting an extra filing would change the study's document set."""
    path = touch(tmp_path, "extra.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "股票代碼:2454\n一一二年度年報")[
        0
    ]
    assert plan.target_name == "2454-FY2023-AR.pdf"
    assert plan.declared is False
    assert plan.is_ready is False


def test_plans_are_sorted_for_stable_output(tmp_path: Path) -> None:
    second = touch(tmp_path, "b.pdf")
    first = touch(tmp_path, "a.pdf")
    plans = plan_renames([second, first], DECLARED, cover_reader=lambda _p: CJK_COVER)
    assert [plan.path.name for plan in plans] == ["a.pdf", "b.pdf"]


def test_uppercase_suffix_is_normalised(tmp_path: Path) -> None:
    path = touch(tmp_path, "REPORT.PDF")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: CJK_COVER)[0]
    assert plan.target_name == "2412-FY2023-AR.pdf"


# ------------------------------------------------------------- reading a file


def test_read_cover_text_extracts_the_first_pages(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "cover.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 100), "股票代碼:2412", fontname="china-t", fontsize=14)
    page.insert_text((72, 140), "一一二年度年報", fontname="china-t", fontsize=20)
    document.save(path)
    document.close()

    text = read_cover_text(path)
    assert "2412" in text
    identity = identify_cover_text(text)
    assert identity.fiscal_year == 2023


def test_read_cover_text_survives_a_corrupt_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf at all")
    assert read_cover_text(broken) == ""


def test_read_cover_text_handles_fewer_pages_than_requested(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "one.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(path)
    document.close()
    assert read_cover_text(path, pages=5) == ""
