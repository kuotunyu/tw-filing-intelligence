"""Acquisition must produce a record that can be re-verified, in both modes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from twfi.io.acquire import (
    ExpectedArtifact,
    count_pdf_pages,
    expected_artifacts,
    fetch_structured_datasets,
    provenance_table,
    register_manual_artifacts,
)
from twfi.io.http import PoliteClient, PolitenessBudget
from twfi.io.manifest import (
    AcquisitionLock,
    AcquisitionRecord,
    DocumentManifest,
    StructuredManifest,
    verify_acquisition,
)

STAMP = "2026-07-31T12:00:00+00:00"
ROWS = [{"公司代號": "2330", "年度": "115", "季別": "1", "營業收入": "1134103440.00"}]


def stamp() -> str:
    return STAMP


def documents_manifest() -> DocumentManifest:
    return DocumentManifest.model_validate(
        {
            "version": 1,
            "documents": [
                {
                    "doc_id": "2330-FY2024-AR",
                    "company": {"name": "台積電", "code": "2330"},
                    "fiscal_year": 2024,
                    "doc_type": "annual_report",
                    "split": "locked",
                    "source_page": "https://doc.twse.com.tw/server-java/t57sb01",
                    "notes": "公司代號 2330, 資料年度 113",
                },
                {
                    "doc_id": "2412-FY2023-AR",
                    "company": {"name": "中華電信", "code": "2412"},
                    "fiscal_year": 2023,
                    "doc_type": "annual_report",
                    "split": "dev",
                    "source_page": "https://doc.twse.com.tw/server-java/t57sb01",
                    "notes": "公司代號 2412, 資料年度 112",
                },
            ],
        }
    )


def structured_manifest() -> StructuredManifest:
    return StructuredManifest.model_validate(
        {
            "version": 1,
            "datasets": [
                {
                    "dataset_id": "twse-openapi-t187ap06_L_ci",
                    "source": "S3",
                    "endpoint": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
                    "description": "上市公司綜合損益表(一般業)",
                },
                {
                    "dataset_id": "mops-xbrl-2330-FY2024",
                    "source": "S2",
                    "endpoint": "https://mops.twse.com.tw/mops/web/t57sb01_q1",
                    "description": "台積電 FY2024 XBRL",
                    "split": "locked",
                },
            ],
        }
    )


def client_returning(payload: bytes, status: int = 200) -> PoliteClient:
    return PoliteClient(
        budget=PolitenessBudget(min_interval_s=0.0),
        transport=httpx.MockTransport(lambda _r: httpx.Response(status, content=payload)),
    )


# ------------------------------------------------------------ expected artifacts


def test_expected_artifacts_covers_documents_and_manual_datasets() -> None:
    artifacts = expected_artifacts(documents_manifest(), structured_manifest())
    assert [artifact.id for artifact in artifacts] == [
        "2330-FY2024-AR",
        "2412-FY2023-AR",
        "mops-xbrl-2330-FY2024",
    ]


def test_openapi_datasets_are_not_expected_by_hand() -> None:
    artifacts = expected_artifacts(documents_manifest(), structured_manifest())
    assert "twse-openapi-t187ap06_L_ci" not in {artifact.id for artifact in artifacts}


def test_documents_are_required_and_xbrl_is_optional() -> None:
    """The protocol works without XBRL; blocking on it would be dishonest."""
    artifacts = {
        artifact.id: artifact
        for artifact in expected_artifacts(documents_manifest(), structured_manifest())
    }
    assert artifacts["2330-FY2024-AR"].required is True
    assert artifacts["mops-xbrl-2330-FY2024"].required is False


# -------------------------------------------------------------------- automated


def test_fetch_records_digest_rows_and_source(tmp_path: Path) -> None:
    payload = json.dumps(ROWS).encode()
    with client_returning(payload) as client:
        lock, messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp
        )

    record = lock.get("twse-openapi-t187ap06_L_ci")
    assert record is not None
    assert record.acquisition == "fetched"
    assert record.sha256 == hashlib.sha256(payload).hexdigest()
    assert record.rows == 1
    assert record.http_status == 200
    assert record.source_url.endswith("t187ap06_L_ci")  # type: ignore[union-attr]
    assert any(message.startswith("OK") for message in messages)
    assert verify_acquisition(lock, tmp_path) == []


def test_fetch_writes_the_file_where_the_lock_says(tmp_path: Path) -> None:
    with client_returning(json.dumps(ROWS).encode()) as client:
        lock, _messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp
        )
    record = lock.get("twse-openapi-t187ap06_L_ci")
    assert record is not None
    assert record.local_path(tmp_path).is_file()


def test_fetch_ignores_manual_datasets(tmp_path: Path) -> None:
    with client_returning(json.dumps(ROWS).encode()) as client:
        lock, _messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp
        )
    assert lock.get("mops-xbrl-2330-FY2024") is None


def test_fetch_honours_the_only_filter(tmp_path: Path) -> None:
    with client_returning(json.dumps(ROWS).encode()) as client:
        lock, messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp, only=["nope"]
        )
    assert lock.records == []
    assert messages == []


def test_a_failing_endpoint_does_not_discard_other_records(tmp_path: Path) -> None:
    existing = AcquisitionLock.model_validate(
        {
            "version": 1,
            "records": [
                {
                    "id": "already-there",
                    "kind": "dataset",
                    "acquisition": "manual",
                    "relative_path": "data/raw/manual/x.zip",
                    "sha256": "b" * 64,
                    "bytes": 1,
                    "retrieved_at": STAMP,
                }
            ],
        }
    )
    with client_returning(b"", status=500) as client:
        lock, messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, existing, now=stamp
        )
    assert any(message.startswith("FAILED") for message in messages)
    assert lock.get("already-there") is not None


def test_json_that_is_not_a_row_array_records_no_row_count(tmp_path: Path) -> None:
    """A JSON object is valid JSON but not a table; row count must stay unknown."""
    with client_returning(b'{"message": "service unavailable"}') as client:
        lock, messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp
        )
    record = lock.get("twse-openapi-t187ap06_L_ci")
    assert record is not None
    assert record.rows is None
    assert all("not JSON" not in message for message in messages)


def test_default_clock_produces_an_iso_timestamp(tmp_path: Path) -> None:
    item = artifact(tmp_path)
    place(tmp_path, item, b"%PDF now")
    lock, _messages, _missing = register_manual_artifacts(
        [item], tmp_path, AcquisitionLock(), page_counter=lambda _p: None
    )
    record = lock.get("2330-FY2024-AR")
    assert record is not None
    assert record.retrieved_at.startswith("20")
    assert "T" in record.retrieved_at


def test_non_json_response_is_flagged_but_still_recorded(tmp_path: Path) -> None:
    with client_returning(b"<html>maintenance</html>") as client:
        lock, messages = fetch_structured_datasets(
            structured_manifest(), client, tmp_path, AcquisitionLock(), now=stamp
        )
    assert any("not JSON" in message for message in messages)
    record = lock.get("twse-openapi-t187ap06_L_ci")
    assert record is not None
    assert record.rows is None


# ----------------------------------------------------------------------- manual


def artifact(
    tmp_path: Path, name: str = "2330-FY2024-AR", *, required: bool = True
) -> ExpectedArtifact:
    return ExpectedArtifact(
        id=name,
        kind="document",
        relative_path=Path("data/raw/manual") / f"{name}.pdf",
        source_page="https://doc.twse.com.tw/server-java/t57sb01",
        hint=f"save as {name}.pdf",
        required=required,
    )


def place(tmp_path: Path, item: ExpectedArtifact, payload: bytes) -> None:
    target = item.local_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)


def test_placed_files_are_hashed_and_recorded(tmp_path: Path) -> None:
    item = artifact(tmp_path)
    place(tmp_path, item, b"%PDF-1.7 content")

    lock, messages, missing = register_manual_artifacts(
        [item], tmp_path, AcquisitionLock(), now=stamp, page_counter=lambda _p: 312
    )

    record = lock.get("2330-FY2024-AR")
    assert record is not None
    assert record.acquisition == "manual"
    assert record.sha256 == hashlib.sha256(b"%PDF-1.7 content").hexdigest()
    assert record.pages == 312
    assert record.source_url is None
    assert missing == []
    assert messages == ["ADDED  2330-FY2024-AR: 16 bytes"]
    assert verify_acquisition(lock, tmp_path) == []


def test_missing_files_are_reported_not_invented(tmp_path: Path) -> None:
    item = artifact(tmp_path)
    lock, messages, missing = register_manual_artifacts([item], tmp_path, AcquisitionLock())
    assert lock.records == []
    assert messages == []
    assert [entry.id for entry in missing] == ["2330-FY2024-AR"]


def test_unchanged_files_are_not_re_recorded(tmp_path: Path) -> None:
    item = artifact(tmp_path)
    place(tmp_path, item, b"%PDF stable")
    lock, _m, _x = register_manual_artifacts(
        [item], tmp_path, AcquisitionLock(), now=stamp, page_counter=lambda _p: 1
    )
    first = lock.get("2330-FY2024-AR")

    lock, messages, _x = register_manual_artifacts(
        [item], tmp_path, lock, now=lambda: "2099-01-01T00:00:00+00:00", page_counter=lambda _p: 1
    )
    assert lock.get("2330-FY2024-AR") == first, "an unchanged file must not restamp the record"
    assert messages == ["OK     2330-FY2024-AR: unchanged (11 bytes)"]


def test_a_replaced_file_is_reported_as_changed(tmp_path: Path) -> None:
    """Silently accepting a different file would break reproducibility claims."""
    item = artifact(tmp_path)
    place(tmp_path, item, b"%PDF first")
    lock, _m, _x = register_manual_artifacts(
        [item], tmp_path, AcquisitionLock(), now=stamp, page_counter=lambda _p: 1
    )

    place(tmp_path, item, b"%PDF second version")
    lock, messages, _x = register_manual_artifacts(
        [item], tmp_path, lock, now=stamp, page_counter=lambda _p: 2
    )
    assert any(message.startswith("CHANGED") for message in messages)
    record = lock.get("2330-FY2024-AR")
    assert record is not None
    assert record.sha256 == hashlib.sha256(b"%PDF second version").hexdigest()


def test_optional_and_required_missing_are_distinguishable(tmp_path: Path) -> None:
    required = artifact(tmp_path, "2330-FY2024-AR", required=True)
    optional = artifact(tmp_path, "2317-FY2024-AR", required=False)
    _lock, _messages, missing = register_manual_artifacts(
        [required, optional], tmp_path, AcquisitionLock()
    )
    assert {entry.id: entry.required for entry in missing} == {
        "2330-FY2024-AR": True,
        "2317-FY2024-AR": False,
    }


# ------------------------------------------------------------------ page counter


def test_page_counter_ignores_non_pdfs(tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    assert count_pdf_pages(target) is None


def test_page_counter_survives_a_corrupt_pdf(tmp_path: Path) -> None:
    """A broken file must not abort the whole acquisition run."""
    target = tmp_path / "broken.pdf"
    target.write_bytes(b"not really a pdf")
    assert count_pdf_pages(target) is None


def test_page_counter_reads_a_real_pdf(tmp_path: Path) -> None:
    pymupdf = pytest.importorskip("pymupdf")
    target = tmp_path / "three.pdf"
    document = pymupdf.open()
    for _ in range(3):
        document.new_page()
    document.save(target)
    document.close()
    assert count_pdf_pages(target) == 3


# -------------------------------------------------------------- provenance table


def test_provenance_table_marks_unacquired_documents() -> None:
    table = provenance_table(documents_manifest(), structured_manifest(), AcquisitionLock())
    assert "**not acquired**" in table
    assert "2330-FY2024-AR" in table
    assert "twse-openapi-t187ap06_L_ci" in table


def test_provenance_table_shows_digests_and_pages() -> None:
    lock = AcquisitionLock.model_validate(
        {
            "version": 1,
            "records": [
                {
                    "id": "2330-FY2024-AR",
                    "kind": "document",
                    "acquisition": "manual",
                    "relative_path": "data/raw/manual/2330-FY2024-AR.pdf",
                    "sha256": "c" * 64,
                    "bytes": 10,
                    "retrieved_at": STAMP,
                    "pages": 312,
                },
                {
                    "id": "twse-openapi-t187ap06_L_ci",
                    "kind": "dataset",
                    "acquisition": "fetched",
                    "relative_path": "data/raw/structured/twse-openapi-t187ap06_L_ci.json",
                    "sha256": "d" * 64,
                    "bytes": 20,
                    "retrieved_at": STAMP,
                    "source_url": "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci",
                    "http_status": 200,
                    "rows": 1045,
                },
            ],
        }
    )
    table = provenance_table(documents_manifest(), structured_manifest(), lock)
    assert "cccccccccccccccc…" in table
    assert "| 312 |" in table
    assert "| 1045 |" in table
    assert "2026-07-31" in table


def test_provenance_table_is_deterministic() -> None:
    args: tuple[Any, ...] = (documents_manifest(), structured_manifest(), AcquisitionLock())
    assert provenance_table(*args) == provenance_table(*args)


def test_acquisition_record_local_path_is_root_relative(tmp_path: Path) -> None:
    record = AcquisitionRecord.model_validate(
        {
            "id": "x",
            "kind": "dataset",
            "acquisition": "manual",
            "relative_path": "data/raw/structured/x.json",
            "sha256": "e" * 64,
            "bytes": 1,
            "retrieved_at": STAMP,
        }
    )
    assert record.local_path(tmp_path) == tmp_path / "data" / "raw" / "structured" / "x.json"
