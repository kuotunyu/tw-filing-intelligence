"""Naming files from the query would mislabel every document by one year."""

from __future__ import annotations

from pathlib import Path

import pytest

from twfi.io.identify import (
    ROC_EPOCH,
    DocumentIdentity,
    find_company_code,
    find_roc_year,
    identify,
    parse_cjk_digits,
    parse_mops_filename,
    pdf_candidates,
    plan_renames,
    read_cover_text,
)

#: Names MOPS actually gave downloads, observed 2026-07-31. Two schemes, one per
#: 資料類型: the annual report carries a filing date, the financial report a quarter.
MOPS_NAME = "2022_2412_20230526F04_20260731_021308.pdf"
FS_NAME = "202404_2317_AI1_20260731_024613.pdf"

#: The cover of that file. Note the listing code is artwork with no text layer,
#: which is why the filename is the primary source.
REAL_COVER = "台灣永續\n世代前行\n一一一年度年報"
CJK_COVER = "股票代碼:2412\n中華電信\n一一二年度年報"
ARABIC_COVER = "股票代碼：2330\n民國113年度年報"


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


# -------------------------------------------------------------- MOPS filenames


def test_the_real_annual_report_filename_parses() -> None:
    parsed = parse_mops_filename(MOPS_NAME)
    assert parsed is not None
    assert parsed.fiscal_year == 2022
    assert parsed.company_code == "2412"
    assert parsed.filed_on == "20230526"
    assert parsed.dtype == "F04"
    assert parsed.kind == "annual_report"


def test_the_real_financial_report_filename_parses() -> None:
    """A different scheme: year+quarter, no filing date."""
    parsed = parse_mops_filename(FS_NAME)
    assert parsed is not None
    assert parsed.fiscal_year == 2024
    assert parsed.quarter == 4
    assert parsed.company_code == "2317"
    assert parsed.dtype == "AI1"
    assert parsed.kind == "financial_report"


@pytest.mark.parametrize(
    ("dtype", "kind"),
    [
        ("F04", "annual_report"),
        ("F18", "annual_report"),
        ("F11", "annual_report"),
        ("F01", None),  # 開會通知
        ("F19", None),  # 僅永續專章
        ("F05", None),  # 股東會議事錄
    ],
)
def test_only_real_annual_report_dtypes_are_accepted(dtype: str, kind: str | None) -> None:
    """An 開會通知 must be refused, not filed as an annual report."""
    parsed = parse_mops_filename(f"2023_2330_20240604{dtype}_x.pdf")
    assert parsed is not None
    assert parsed.kind == kind


def test_an_unknown_dtype_yields_no_filename() -> None:
    found = identify("2023_2330_20240604F01_x.pdf", "")
    assert found.company_code == "2330"
    assert found.fiscal_year == 2023
    assert found.kind is None
    assert found.expected_filename() is None
    assert "document kind" in found.missing()


def test_the_filename_year_is_the_fiscal_year_not_the_index_year() -> None:
    """This is the whole point: 資料年度 112 produced a FY2022 document."""
    parsed = parse_mops_filename(MOPS_NAME)
    assert parsed is not None
    assert parsed.fiscal_year == 2022
    assert parsed.fiscal_year - ROC_EPOCH == 111, "cover reads 民國111年度"


@pytest.mark.parametrize(
    "name",
    [
        "report.pdf",
        "report (1).pdf",
        "2412_2022_20230526F04.pdf",  # fields transposed: year must come first
        "1990_2412_20230526F04_x.pdf",  # implausible year
        "20221_2412_20230526F04_x.pdf",
        "2022_241_20230526F04_x.pdf",  # three-digit code
        "",
    ],
)
def test_non_mops_names_are_refused(name: str) -> None:
    assert parse_mops_filename(name) is None


def test_a_browser_renamed_copy_falls_back_to_the_cover() -> None:
    """The cover gives company and year but cannot say annual report vs statements."""
    found = identify("report (1).pdf", CJK_COVER)
    assert found.evidence == ("cover",)
    assert found.company_code == "2412"
    assert found.fiscal_year == 2023
    assert found.kind is None
    assert found.expected_filename() is None, "document kind is not knowable from a cover"


# --------------------------------------------------------------- company code


@pytest.mark.parametrize(
    "text",
    ["股票代碼:2412", "股票代碼：2412", "股票代號 2412", "證券代碼：2412", "公司代號: 2412"],
)
def test_company_code_is_found(text: str) -> None:
    assert find_company_code(text) == "2412"


def test_no_company_code_returns_none() -> None:
    assert find_company_code("年度報告書\n第一章") is None


def test_an_artwork_cover_yields_no_code() -> None:
    """The observed 中華電信 cover prints its code as an image, not as text."""
    assert find_company_code(REAL_COVER) is None


def test_a_bare_four_digit_number_is_not_taken_as_a_code() -> None:
    assert find_company_code("民國112年度年報\n共 1234 頁") is None


# ------------------------------------------------------------------ roc year


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (REAL_COVER, 111),
        (CJK_COVER, 112),
        (ARABIC_COVER, 113),
        ("民國 112 年度合併財務報告", 112),
        ("112年度年報", 112),
        ("一一三年度年報", 113),
        ("民國一一二年度", 112),
    ],
)
def test_year_patterns(text: str, expected: int) -> None:
    assert find_roc_year(text) == expected


@pytest.mark.parametrize("text", ["第 5 年度", "年報", "", "1911年度年報", "999年度年報"])
def test_implausible_years_are_rejected(text: str) -> None:
    assert find_roc_year(text) is None


# ------------------------------------------------------------------- identity


def test_the_real_file_is_identified_from_its_filename_alone() -> None:
    """The cover has no readable code, so the filename has to carry it."""
    found = identify(MOPS_NAME, REAL_COVER)
    assert found.company_code == "2412"
    assert found.fiscal_year == 2022
    assert found.roc_year == 111
    assert found.evidence == ("filename", "cover")
    assert found.conflict == ""
    assert found.expected_filename() == "2412-FY2022-AR.pdf"


def test_the_filename_wins_but_a_disagreement_is_reported() -> None:
    """Silently picking a winner is how a mislabelled document enters a locked set."""
    found = identify("2022_2412_20230526F04_x.pdf", "股票代碼:2412\n一一二年度年報")
    assert found.conflict == "filename says FY2022 but the cover says FY2023"
    assert found.is_complete is False
    assert found.expected_filename() is None


def test_a_company_code_disagreement_is_reported() -> None:
    found = identify("2022_2412_20230526F04_x.pdf", "股票代碼:2330\n一一一年度年報")
    assert "filename says 2412 but the cover says 2330" in found.conflict


def test_roc_year_is_derived_when_the_cover_is_silent() -> None:
    found = identify(MOPS_NAME, "no year here")
    assert found.roc_year == 111
    assert found.evidence == ("filename",)


def test_nothing_readable_yields_an_empty_identity() -> None:
    found = identify("mystery.pdf", "")
    assert found.is_complete is False
    assert found.evidence == ()
    assert found.expected_filename() is None


def test_describe_is_human_readable() -> None:
    assert "代號 2412" in identify(MOPS_NAME, REAL_COVER).describe()
    assert "FY2022" in identify(MOPS_NAME, REAL_COVER).describe()
    assert "from filename+cover" in identify(MOPS_NAME, REAL_COVER).describe()
    assert "from none" in identify("x.pdf", "").describe()


def test_incomplete_identity_has_no_filename() -> None:
    assert DocumentIdentity(company_code="2412", kind="annual_report").expected_filename() is None
    assert DocumentIdentity(fiscal_year=2023, kind="annual_report").expected_filename() is None
    assert DocumentIdentity(company_code="2412", fiscal_year=2023).expected_filename() is None


def test_expected_filenames_match_the_manifest_convention() -> None:
    assert identify(MOPS_NAME, REAL_COVER).expected_filename() == "2412-FY2022-AR.pdf"
    assert identify(FS_NAME, "").expected_filename() == "2317-FY2024-FS.pdf"


def test_describe_names_the_document_kind() -> None:
    assert "/ AR " in identify(MOPS_NAME, REAL_COVER).describe()
    assert "/ FS " in identify(FS_NAME, "").describe()
    assert "/ ? " in identify("x.pdf", "").describe()


# ------------------------------------------------------------- pdf candidates


def touch(directory: Path, name: str) -> Path:
    target = directory / name
    target.write_bytes(b"%PDF-1.7 stub")
    return target


def test_candidates_do_not_duplicate_on_a_case_insensitive_filesystem(tmp_path: Path) -> None:
    """Globbing *.pdf and *.PDF separately listed the same Windows file twice."""
    touch(tmp_path, "a.pdf")
    touch(tmp_path, "B.PDF")
    found = pdf_candidates(tmp_path)
    assert len(found) == 2
    assert len({path.resolve() for path in found}) == 2


def test_candidates_ignore_other_files_and_directories(tmp_path: Path) -> None:
    touch(tmp_path, "keep.pdf")
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    assert [path.name for path in pdf_candidates(tmp_path)] == ["keep.pdf"]


def test_candidates_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert pdf_candidates(tmp_path / "absent") == []


# ---------------------------------------------------------------- rename plan


DECLARED = {"2412-FY2023-AR.pdf", "2330-FY2024-AR.pdf", "2317-FY2024-FS.pdf"}


def test_a_financial_report_download_gets_an_fs_name(tmp_path: Path) -> None:
    path = touch(tmp_path, FS_NAME)
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "一一三年度")[0]
    assert plan.target_name == "2317-FY2024-FS.pdf"
    assert plan.declared is True
    assert plan.is_ready is True


def test_an_already_declared_name_is_accepted_without_re_identification(tmp_path: Path) -> None:
    """Renaming discards the MOPS filename, so a second pass must not re-derive it.

    Regression: after --apply, every renamed file was reported as unreadable because
    its identity evidence had been renamed away. The declaration is the identity at
    that point, and the digest in acquisition.lock.yaml is what guards it.
    """
    path = touch(tmp_path, "2412-FY2023-AR.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "")[0]
    assert plan.problem == ""
    assert plan.declared is True
    assert plan.needs_rename is False
    assert plan.is_ready is True
    assert plan.identity.evidence == ("declared name",)


def test_a_declared_name_is_not_re_read_from_disk(tmp_path: Path) -> None:
    """The shortcut must not even open the file."""
    path = touch(tmp_path, "2412-FY2023-AR.pdf")

    def explode(_path: Path) -> str:  # pragma: no cover - must not be called
        raise AssertionError("a declared name must not be re-identified")

    assert plan_renames([path], DECLARED, cover_reader=explode)[0].is_ready is True


def test_a_mops_download_gets_the_right_target(tmp_path: Path) -> None:
    path = touch(tmp_path, "2023_2412_20240515F04_20260731_120000.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "一一二年度年報")[0]
    assert plan.target_name == "2412-FY2023-AR.pdf"
    assert plan.needs_rename is True
    assert plan.declared is True
    assert plan.is_ready is True


def test_the_observed_file_is_reported_as_undeclared(tmp_path: Path) -> None:
    """FY2022 is not in the study's seven documents; it must not be quietly accepted."""
    path = touch(tmp_path, MOPS_NAME)
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: REAL_COVER)[0]
    assert plan.target_name == "2412-FY2022-AR.pdf"
    assert plan.declared is False
    assert plan.is_ready is False


def test_an_already_correct_name_needs_no_rename(tmp_path: Path) -> None:
    path = touch(tmp_path, "2412-FY2023-AR.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: CJK_COVER)[0]
    assert plan.needs_rename is False
    assert plan.is_ready is True


def test_an_unreadable_file_is_reported_not_guessed(tmp_path: Path) -> None:
    path = touch(tmp_path, "mystery.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "")[0]
    assert plan.target_name is None
    assert "company code" in plan.problem
    assert "fiscal year" in plan.problem
    assert plan.is_ready is False


def test_a_partially_readable_file_names_what_is_missing(tmp_path: Path) -> None:
    path = touch(tmp_path, "mystery.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "股票代碼:2412")[0]
    assert plan.problem.startswith("could not read fiscal year")


def test_a_conflict_becomes_the_reported_problem(tmp_path: Path) -> None:
    path = touch(tmp_path, "2022_2412_20230526F04_x.pdf")
    plan = plan_renames([path], DECLARED, cover_reader=lambda _p: "一一二年度年報")[0]
    assert "but the cover says" in plan.problem
    assert plan.target_name is None


def test_plans_are_sorted_for_stable_output(tmp_path: Path) -> None:
    second = touch(tmp_path, "b.pdf")
    first = touch(tmp_path, "a.pdf")
    plans = plan_renames([second, first], DECLARED, cover_reader=lambda _p: CJK_COVER)
    assert [plan.path.name for plan in plans] == ["a.pdf", "b.pdf"]


def test_uppercase_suffix_is_normalised(tmp_path: Path) -> None:
    path = touch(tmp_path, "2023_2412_20240531F04_X.PDF")
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

    found = identify("whatever.pdf", read_cover_text(path))
    assert found.company_code == "2412"
    assert found.fiscal_year == 2023


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
