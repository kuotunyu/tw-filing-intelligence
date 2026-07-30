"""Manifests are the provenance, because the filings themselves are not committed."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

from twfi.errors import HashMismatchError, ManifestError
from twfi.io.manifest import (
    AcquisitionLock,
    AcquisitionRecord,
    DocumentManifest,
    DocumentRecord,
    StructuredDataset,
    StructuredManifest,
    assert_acquisition_matches,
    dump_yaml_model,
    load_acquisition_lock,
    load_document_manifest,
    load_structured_manifest,
    verify_acquisition,
)

SOURCE_PAGE = "https://doc.twse.com.tw/server-java/t57sb01"
OPENAPI = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
DIGEST = "a" * 64
STAMP = "2026-07-31T10:00:00+00:00"


def document(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "doc_id": "2330-FY2024-AR",
        "company": {"name": "台積電", "code": "2330"},
        "fiscal_year": 2024,
        "doc_type": "annual_report",
        "split": "locked",
        "source_page": SOURCE_PAGE,
        "notes": "Save as data/raw/manual/2330-FY2024-AR.pdf",
    }
    record.update(overrides)
    return record


def dataset(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "dataset_id": "twse-openapi-t187ap06_L_ci",
        "source": "S3",
        "endpoint": OPENAPI,
        "description": "上市公司綜合損益表(一般業)",
    }
    record.update(overrides)
    return record


def acquired(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": "2330-FY2024-AR",
        "kind": "document",
        "acquisition": "manual",
        "relative_path": "data/raw/manual/2330-FY2024-AR.pdf",
        "sha256": DIGEST,
        "bytes": 1234,
        "retrieved_at": STAMP,
    }
    record.update(overrides)
    return record


# --------------------------------------------------------------- declarations


def test_a_declared_document_is_valid() -> None:
    record = DocumentRecord.model_validate(document())
    assert record.filename == "2330-FY2024-AR.pdf"
    assert record.relative_path.as_posix() == "data/raw/manual/2330-FY2024-AR.pdf"


def test_local_path_is_repo_relative(tmp_path: Path) -> None:
    record = DocumentRecord.model_validate(document())
    assert record.local_path(tmp_path) == tmp_path / "data" / "raw" / "manual" / record.filename


def test_xbrl_documents_get_a_zip_filename() -> None:
    record = DocumentRecord.model_validate(document(doc_id="2330-FY2024-XBRL", doc_type="xbrl"))
    assert record.filename == "2330-FY2024-XBRL.zip"


def test_declarations_carry_no_provenance_fields() -> None:
    """Provenance belongs in the lock file; a declaration must not duplicate it."""
    for field in ("sha256", "retrieved_at", "acquisition", "resolved_url"):
        with pytest.raises(ValueError, match="extra"):
            DocumentRecord.model_validate(document(**{field: DIGEST}))


@pytest.mark.parametrize(
    "url",
    [
        "http://mops.twse.com.tw/x",
        "https://evil.example.com/x",
        "https://127.0.0.1/x",
        "https://mops.twse.com.tw:8443/x",
    ],
)
def test_declared_urls_obey_the_http_allowlist(url: str) -> None:
    """A manifest must not be able to smuggle in an off-allowlist target."""
    with pytest.raises(ValueError, match="source_page"):
        DocumentRecord.model_validate(document(source_page=url))


def test_split_must_match_the_protocol_assignment() -> None:
    with pytest.raises(ValueError, match="assigned to the 'locked' split"):
        DocumentRecord.model_validate(document(split="dev"))


def test_dev_company_cannot_be_declared_locked() -> None:
    with pytest.raises(ValueError, match="assigned to the 'dev' split"):
        DocumentRecord.model_validate(
            document(
                doc_id="2412-FY2023-AR",
                company={"name": "中華電信", "code": "2412"},
                fiscal_year=2023,
                split="locked",
            )
        )


def test_companies_outside_the_study_are_refused() -> None:
    with pytest.raises(ValueError, match="not part of the pre-registered study"):
        DocumentRecord.model_validate(
            document(doc_id="2454-FY2024-AR", company={"name": "聯發科", "code": "2454"})
        )


def test_doc_id_must_encode_company_and_year() -> None:
    with pytest.raises(ValueError, match="must start with"):
        DocumentRecord.model_validate(document(doc_id="2330-FY2023-AR", fiscal_year=2024))


def test_duplicate_doc_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate doc_id"):
        DocumentManifest.model_validate({"version": 1, "documents": [document(), document()]})


def test_manifest_queries_by_split() -> None:
    manifest = DocumentManifest.model_validate(
        {
            "version": 1,
            "documents": [
                document(),
                document(
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
    assert manifest.get("2330-FY2024-AR").fiscal_year == 2024
    with pytest.raises(KeyError):
        manifest.get("9999-FY2024-AR")


# ------------------------------------------------------------- structured decls


def test_openapi_datasets_are_automated_and_land_in_the_structured_dir() -> None:
    record = StructuredDataset.model_validate(dataset())
    assert record.automated is True
    assert record.relative_path.as_posix() == (
        "data/raw/structured/twse-openapi-t187ap06_L_ci.json"
    )


def test_xbrl_datasets_are_manual_and_land_in_the_manual_dir(tmp_path: Path) -> None:
    record = StructuredDataset.model_validate(
        dataset(
            dataset_id="mops-xbrl-2330-FY2024",
            source="S2",
            endpoint="https://mops.twse.com.tw/mops/web/t57sb01_q1",
        )
    )
    assert record.automated is False
    assert record.relative_path.as_posix() == "data/raw/manual/mops-xbrl-2330-FY2024.zip"
    assert record.local_path(tmp_path).parent.name == "manual"


def test_structured_manifest_partitions_automated_and_manual() -> None:
    manifest = StructuredManifest.model_validate(
        {
            "version": 1,
            "datasets": [
                dataset(),
                dataset(
                    dataset_id="mops-xbrl-2330-FY2024",
                    source="S2",
                    endpoint="https://mops.twse.com.tw/mops/web/t57sb01_q1",
                ),
            ],
        }
    )
    assert [d.dataset_id for d in manifest.automated()] == ["twse-openapi-t187ap06_L_ci"]
    assert [d.dataset_id for d in manifest.manual()] == ["mops-xbrl-2330-FY2024"]


def test_structured_manifest_refuses_foreign_endpoints() -> None:
    with pytest.raises(ValueError, match="endpoint"):
        StructuredDataset.model_validate(dataset(endpoint="https://evil.example.com/x"))


def test_duplicate_dataset_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate dataset_id"):
        StructuredManifest.model_validate({"version": 1, "datasets": [dataset(), dataset()]})


# --------------------------------------------------------------- acquisition lock


def test_an_acquisition_record_needs_everything_needed_to_re_verify_it() -> None:
    for field in ("sha256", "bytes", "retrieved_at", "relative_path"):
        payload = acquired()
        del payload[field]
        with pytest.raises(ValueError):
            AcquisitionRecord.model_validate(payload)


def test_an_automated_fetch_must_record_where_it_came_from() -> None:
    with pytest.raises(ValueError, match="must record source_url and http_status"):
        AcquisitionRecord.model_validate(acquired(acquisition="fetched", kind="dataset"))


def test_an_automated_fetch_with_full_provenance_is_valid() -> None:
    record = AcquisitionRecord.model_validate(
        acquired(
            id="twse-openapi-t187ap06_L_ci",
            kind="dataset",
            acquisition="fetched",
            relative_path="data/raw/structured/twse-openapi-t187ap06_L_ci.json",
            source_url=OPENAPI,
            http_status=200,
            rows=1045,
        )
    )
    assert record.rows == 1045


def test_manual_records_do_not_need_a_source_url() -> None:
    """Manual placement is the sanctioned fallback, so it must be representable."""
    record = AcquisitionRecord.model_validate(acquired())
    assert record.source_url is None
    assert record.acquisition == "manual"


def test_acquisition_source_urls_obey_the_allowlist() -> None:
    with pytest.raises(ValueError, match="source_url"):
        AcquisitionRecord.model_validate(
            acquired(
                acquisition="fetched", source_url="https://evil.example.com/x", http_status=200
            )
        )


def test_malformed_hashes_are_refused() -> None:
    with pytest.raises(ValueError):
        AcquisitionRecord.model_validate(acquired(sha256="not-a-hash"))


def test_upsert_replaces_and_keeps_a_stable_order() -> None:
    lock = AcquisitionLock()
    lock = lock.upsert(AcquisitionRecord.model_validate(acquired(id="b-doc")))
    lock = lock.upsert(AcquisitionRecord.model_validate(acquired(id="a-doc")))
    assert [record.id for record in lock.records] == ["a-doc", "b-doc"]

    lock = lock.upsert(AcquisitionRecord.model_validate(acquired(id="a-doc", bytes=99)))
    assert len(lock.records) == 2
    updated = lock.get("a-doc")
    assert updated is not None
    assert updated.bytes == 99
    assert lock.get("missing") is None
    assert lock.ids == {"a-doc", "b-doc"}


def test_duplicate_acquisition_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate acquisition id"):
        AcquisitionLock.model_validate({"version": 1, "records": [acquired(), acquired()]})


# -------------------------------------------------------------------- load / dump


def test_load_and_dump_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    original = DocumentManifest.model_validate({"version": 1, "documents": [document()]})
    dump_yaml_model(original, path)

    assert "台積電" in path.read_text(encoding="utf-8"), "Chinese names must not be escaped"
    assert load_document_manifest(path) == original


def test_dump_can_prepend_a_generated_header(tmp_path: Path) -> None:
    path = tmp_path / "acquisition.lock.yaml"
    dump_yaml_model(AcquisitionLock(), path, header="GENERATED\ndo not edit")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# GENERATED\n# do not edit\n")


def test_dumped_yaml_keeps_field_order(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    dump_yaml_model(
        DocumentManifest.model_validate({"version": 1, "documents": [document()]}), path
    )
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(payload["documents"][0])[:4] == ["doc_id", "company", "fiscal_year", "doc_type"]


def test_missing_lock_means_nothing_acquired_yet(tmp_path: Path) -> None:
    """A first run must not need the file to exist."""
    assert load_acquisition_lock(tmp_path / "absent.yaml").records == []


def test_load_reports_a_missing_manifest(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="no manifest at"):
        load_document_manifest(tmp_path / "absent.yaml")


def test_load_reports_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "documents.yaml"
    path.write_text("documents: [\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="not valid YAML"):
        load_document_manifest(path)


@pytest.mark.parametrize(
    ("loader", "payload", "message"),
    [
        (
            load_document_manifest,
            {"version": 1, "documents": [{"doc_id": "x"}]},
            "document manifest",
        ),
        (load_structured_manifest, {"version": 1, "datasets": [{"dataset_id": "x"}]}, "structured"),
        (load_acquisition_lock, {"version": 1, "records": [{"id": "x"}]}, "acquisition lock"),
    ],
)
def test_loaders_report_schema_violations(
    tmp_path: Path, loader: Any, payload: dict[str, Any], message: str
) -> None:
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match=message):
        loader(path)


# ---------------------------------------------------------------- verification


def _place(repo_root: Path, relative: str, payload: bytes) -> str:
    target = repo_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_verify_passes_when_the_file_matches(tmp_path: Path) -> None:
    payload = b"%PDF-1.7 real content"
    digest = _place(tmp_path, "data/raw/manual/2330-FY2024-AR.pdf", payload)
    lock = AcquisitionLock.model_validate(
        {"version": 1, "records": [acquired(sha256=digest, bytes=len(payload))]}
    )
    assert verify_acquisition(lock, tmp_path) == []
    assert_acquisition_matches(lock, tmp_path)


def test_verify_reports_a_missing_file(tmp_path: Path) -> None:
    lock = AcquisitionLock.model_validate({"version": 1, "records": [acquired()]})
    problems = verify_acquisition(lock, tmp_path)
    assert len(problems) == 1
    assert "is missing" in problems[0]


def test_verify_reports_a_hash_mismatch(tmp_path: Path) -> None:
    _place(tmp_path, "data/raw/manual/2330-FY2024-AR.pdf", b"%PDF tampered")
    lock = AcquisitionLock.model_validate(
        {"version": 1, "records": [acquired(sha256=DIGEST, bytes=13)]}
    )
    assert "sha256 mismatch" in verify_acquisition(lock, tmp_path)[0]
    with pytest.raises(HashMismatchError, match="does not match the lock"):
        assert_acquisition_matches(lock, tmp_path)


def test_verify_reports_a_size_mismatch(tmp_path: Path) -> None:
    payload = b"%PDF-1.7 real content"
    digest = _place(tmp_path, "data/raw/manual/2330-FY2024-AR.pdf", payload)
    lock = AcquisitionLock.model_validate(
        {"version": 1, "records": [acquired(sha256=digest, bytes=len(payload) + 1)]}
    )
    assert "size mismatch" in verify_acquisition(lock, tmp_path)[0]


def test_verify_reports_what_has_not_been_acquired_yet(tmp_path: Path) -> None:
    """This is how G1 evidence is assembled: declared minus acquired."""
    lock = AcquisitionLock()
    problems = verify_acquisition(lock, tmp_path, expected_ids={"2330-FY2024-AR", "1301-FY2023-AR"})
    assert problems == [
        "1301-FY2023-AR: declared but not acquired yet",
        "2330-FY2024-AR: declared but not acquired yet",
    ]


def test_empty_declarations_are_valid() -> None:
    assert DocumentManifest().documents == []
    assert StructuredManifest().datasets == []
    assert AcquisitionLock().records == []
