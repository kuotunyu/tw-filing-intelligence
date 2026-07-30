"""Manifests are the provenance, because the filings themselves are not committed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from twfi.errors import HashMismatchError, ManifestError
from twfi.io.manifest import (
    DocumentManifest,
    DocumentRecord,
    StructuredManifest,
    assert_local_documents_match,
    dump_manifest,
    load_document_manifest,
    load_structured_manifest,
    verify_local_documents,
)

SOURCE_PAGE = "https://mops.twse.com.tw/mops/web/t57sb01_q1"
RESOLVED = "https://doc.twse.com.tw/pdf/2330-FY2024.pdf"
DIGEST = "a" * 64


def pending_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "doc_id": "2330-FY2024-AR",
        "company": {"name": "台積電", "code": "2330"},
        "fiscal_year": 2024,
        "doc_type": "annual_report",
        "split": "locked",
        "source_page": SOURCE_PAGE,
        "acquisition": "pending",
    }
    record.update(overrides)
    return record


def fetched_record(**overrides: Any) -> dict[str, Any]:
    record = pending_record()
    record.update(
        {
            "acquisition": "fetched",
            "resolved_url": RESOLVED,
            "sha256": DIGEST,
            "bytes": 1234,
            "retrieved_at": "2026-07-31T10:00:00+00:00",
            "http_status": 200,
        }
    )
    record.update(overrides)
    return record


# ------------------------------------------------------------------- happy paths


def test_a_pending_record_is_valid() -> None:
    record = DocumentRecord.model_validate(pending_record())
    assert record.acquisition == "pending"
    assert record.filename == "2330-FY2024-AR.pdf"


def test_a_fetched_record_is_valid() -> None:
    record = DocumentRecord.model_validate(fetched_record())
    assert record.sha256 == DIGEST
    assert record.http_status == 200


def test_a_manual_record_does_not_need_a_resolved_url() -> None:
    """The manual fallback exists because some documents cannot be fetched politely."""
    record = DocumentRecord.model_validate(
        pending_record(
            acquisition="manual",
            sha256=DIGEST,
            bytes=99,
            retrieved_at="2026-07-31T10:00:00+00:00",
        )
    )
    assert record.acquisition == "manual"
    assert record.resolved_url is None


def test_xbrl_documents_get_a_zip_filename() -> None:
    record = DocumentRecord.model_validate(
        pending_record(doc_id="2330-FY2024-XBRL", doc_type="xbrl")
    )
    assert record.filename == "2330-FY2024-XBRL.zip"


def test_local_path_is_organised_by_document_type(tmp_path: Path) -> None:
    record = DocumentRecord.model_validate(pending_record())
    assert record.local_path(tmp_path) == tmp_path / "annual_report" / "2330-FY2024-AR.pdf"


# ------------------------------------------------------------------ url allowlist


@pytest.mark.parametrize(
    "url",
    [
        "http://mops.twse.com.tw/x",
        "https://evil.example.com/x",
        "https://127.0.0.1/x",
        "https://mops.twse.com.tw:8443/x",
    ],
)
def test_manifest_urls_obey_the_http_allowlist(url: str) -> None:
    """A manifest must not be able to smuggle in an off-allowlist target."""
    with pytest.raises(ValueError, match="source_page"):
        DocumentRecord.model_validate(pending_record(source_page=url))


def test_resolved_url_is_validated_too() -> None:
    with pytest.raises(ValueError, match="resolved_url"):
        DocumentRecord.model_validate(fetched_record(resolved_url="https://evil.example.com/a.pdf"))


# ---------------------------------------------------------------- split integrity


def test_split_must_match_the_protocol_assignment() -> None:
    with pytest.raises(ValueError, match="assigned to the 'locked' split"):
        DocumentRecord.model_validate(pending_record(split="dev"))


def test_dev_company_cannot_be_declared_locked() -> None:
    with pytest.raises(ValueError, match="assigned to the 'dev' split"):
        DocumentRecord.model_validate(
            pending_record(
                doc_id="2412-FY2023-AR",
                company={"name": "中華電信", "code": "2412"},
                fiscal_year=2023,
                split="locked",
            )
        )


def test_companies_outside_the_study_are_refused() -> None:
    with pytest.raises(ValueError, match="not part of the pre-registered study"):
        DocumentRecord.model_validate(
            pending_record(
                doc_id="2454-FY2024-AR",
                company={"name": "聯發科", "code": "2454"},
            )
        )


def test_doc_id_must_encode_company_and_year() -> None:
    with pytest.raises(ValueError, match="must start with"):
        DocumentRecord.model_validate(pending_record(doc_id="2330-FY2023-AR", fiscal_year=2024))


# --------------------------------------------------------- half-recorded states


def test_pending_record_may_not_carry_a_hash() -> None:
    with pytest.raises(ValueError, match="half-recorded"):
        DocumentRecord.model_validate(pending_record(sha256=DIGEST))


@pytest.mark.parametrize(
    "dropped", ["sha256", "bytes", "retrieved_at", "resolved_url", "http_status"]
)
def test_fetched_record_requires_full_provenance(dropped: str) -> None:
    record = fetched_record()
    record[dropped] = None
    with pytest.raises(ValueError, match="are missing"):
        DocumentRecord.model_validate(record)


def test_malformed_hashes_are_refused() -> None:
    with pytest.raises(ValueError):
        DocumentRecord.model_validate(fetched_record(sha256="not-a-hash"))


def test_unknown_fields_are_refused() -> None:
    """Typos in a manifest must fail loudly rather than be silently ignored."""
    with pytest.raises(ValueError):
        DocumentRecord.model_validate(pending_record(sha_256=DIGEST))


# ------------------------------------------------------------------- collections


def test_duplicate_doc_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate doc_id"):
        DocumentManifest.model_validate(
            {"version": 1, "documents": [pending_record(), pending_record()]}
        )


def test_manifest_queries_by_split() -> None:
    manifest = DocumentManifest.model_validate(
        {
            "version": 1,
            "documents": [
                pending_record(),
                pending_record(
                    doc_id="2412-FY2023-AR",
                    company={"name": "中華電信", "code": "2412"},
                    fiscal_year=2023,
                    split="dev",
                ),
            ],
        }
    )
    assert manifest.company_codes("dev") == {"2412"}
    assert manifest.company_codes("locked") == {"2330"}
    assert len(manifest.by_split("locked")) == 1


def test_dev_and_locked_documents_never_share_a_company() -> None:
    manifest = DocumentManifest.model_validate(
        {
            "version": 1,
            "documents": [
                pending_record(),
                pending_record(
                    doc_id="1301-FY2023-AR",
                    company={"name": "台塑", "code": "1301"},
                    fiscal_year=2023,
                    split="dev",
                ),
            ],
        }
    )
    assert not manifest.company_codes("dev") & manifest.company_codes("locked")


# -------------------------------------------------------------------- load / dump


def test_load_and_dump_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    original = DocumentManifest.model_validate({"version": 1, "documents": [fetched_record()]})
    dump_manifest(original, path)

    assert "台積電" in path.read_text(encoding="utf-8"), "Chinese names must not be escaped"
    assert load_document_manifest(path) == original


def test_dumped_yaml_keeps_field_order(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    dump_manifest(
        DocumentManifest.model_validate({"version": 1, "documents": [pending_record()]}), path
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(payload["documents"][0])[:4] == ["doc_id", "company", "fiscal_year", "doc_type"]


def test_load_reports_a_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no manifest at"):
        load_document_manifest(tmp_path / "absent.yaml")


def test_load_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    path.write_text("documents: [\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid YAML"):
        load_document_manifest(path)


def test_load_reports_schema_violations(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "documents": [{"doc_id": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="not a valid document manifest"):
        load_document_manifest(path)


# ------------------------------------------------------------------- structured


def test_structured_manifest_validates_endpoints(tmp_path: Path) -> None:
    path = tmp_path / "structured.yaml"
    manifest = StructuredManifest.model_validate(
        {
            "version": 1,
            "datasets": [
                {
                    "dataset_id": "twse-openapi-swagger",
                    "source": "S3",
                    "endpoint": "https://openapi.twse.com.tw/v1/swagger.json",
                }
            ],
        }
    )
    dump_manifest(manifest, path)
    assert load_structured_manifest(path) == manifest


def test_structured_manifest_refuses_foreign_endpoints() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        StructuredManifest.model_validate(
            {
                "version": 1,
                "datasets": [
                    {"dataset_id": "x", "source": "S3", "endpoint": "https://evil.example.com/x"}
                ],
            }
        )


def test_structured_dataset_requires_provenance_once_acquired() -> None:
    with pytest.raises(ValueError, match="are missing"):
        StructuredManifest.model_validate(
            {
                "version": 1,
                "datasets": [
                    {
                        "dataset_id": "x",
                        "source": "S3",
                        "endpoint": "https://openapi.twse.com.tw/v1/x",
                        "acquisition": "fetched",
                    }
                ],
            }
        )


def test_fully_recorded_structured_dataset_is_valid() -> None:
    manifest = StructuredManifest.model_validate(
        {
            "version": 1,
            "datasets": [
                {
                    "dataset_id": "twse-openapi-swagger",
                    "source": "S3",
                    "endpoint": "https://openapi.twse.com.tw/v1/swagger.json",
                    "acquisition": "fetched",
                    "sha256": DIGEST,
                    "bytes": 4096,
                    "rows": 0,
                    "retrieved_at": "2026-07-31T10:00:00+00:00",
                    "http_status": 200,
                }
            ],
        }
    )
    assert manifest.datasets[0].acquisition == "fetched"


def test_structured_load_reports_schema_violations(tmp_path: Path) -> None:
    path = tmp_path / "structured.yaml"
    path.write_text(
        yaml.safe_dump({"version": 1, "datasets": [{"dataset_id": "x"}]}), encoding="utf-8"
    )
    with pytest.raises(ManifestError, match="not a valid structured manifest"):
        load_structured_manifest(path)


def test_duplicate_dataset_ids_are_refused() -> None:
    dataset = {"dataset_id": "x", "source": "S3", "endpoint": "https://openapi.twse.com.tw/v1/x"}
    with pytest.raises(ValueError, match="duplicate dataset_id"):
        StructuredManifest.model_validate({"version": 1, "datasets": [dataset, dict(dataset)]})


# --------------------------------------------------------------- local file check


def _write_document(raw_root: Path, payload: bytes) -> str:
    target = raw_root / "annual_report" / "2330-FY2024-AR.pdf"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_verify_passes_when_the_file_matches(tmp_path: Path) -> None:
    payload = b"%PDF-1.7 real content"
    digest = _write_document(tmp_path, payload)
    manifest = DocumentManifest.model_validate(
        {"version": 1, "documents": [fetched_record(sha256=digest, bytes=len(payload))]}
    )
    assert verify_local_documents(manifest, tmp_path) == []
    assert_local_documents_match(manifest, tmp_path)


def test_verify_reports_a_missing_file(tmp_path: Path) -> None:
    manifest = DocumentManifest.model_validate({"version": 1, "documents": [fetched_record()]})
    problems = verify_local_documents(manifest, tmp_path)
    assert len(problems) == 1
    assert "is missing" in problems[0]


def test_verify_reports_a_hash_mismatch(tmp_path: Path) -> None:
    _write_document(tmp_path, b"%PDF tampered")
    manifest = DocumentManifest.model_validate(
        {"version": 1, "documents": [fetched_record(sha256=DIGEST, bytes=13)]}
    )
    problems = verify_local_documents(manifest, tmp_path)
    assert "sha256 mismatch" in problems[0]

    with pytest.raises(HashMismatchError, match="do not match the manifest"):
        assert_local_documents_match(manifest, tmp_path)


def test_verify_reports_a_size_mismatch(tmp_path: Path) -> None:
    payload = b"%PDF-1.7 real content"
    digest = _write_document(tmp_path, payload)
    manifest = DocumentManifest.model_validate(
        {"version": 1, "documents": [fetched_record(sha256=digest, bytes=len(payload) + 1)]}
    )
    problems = verify_local_documents(manifest, tmp_path)
    assert "size mismatch" in problems[0]


def test_verify_skips_pending_records(tmp_path: Path) -> None:
    manifest = DocumentManifest.model_validate({"version": 1, "documents": [pending_record()]})
    assert verify_local_documents(manifest, tmp_path) == []


def test_empty_manifests_are_valid() -> None:
    assert DocumentManifest().documents == []
    assert StructuredManifest().datasets == []
