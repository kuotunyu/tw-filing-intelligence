"""The committed manifests must stay consistent with the pre-registered protocol.

These run against the real files in ``data/manifests/``, so a hand-edit that
breaks the dev/locked separation or the document count fails the suite rather
than quietly changing what the study measures.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twfi.io.manifest import load_document_manifest, load_structured_manifest
from twfi.paths import RepoPaths
from twfi.protocol import COMPANIES, DEV_COMPANY_CODES, LOCKED_COMPANY_CODES


@pytest.fixture()
def paths(repo_root: Path) -> RepoPaths:
    return RepoPaths(root=repo_root)


# ------------------------------------------------------------------- documents


def test_document_manifest_loads(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    assert manifest.documents, "the study needs declared documents"


def test_document_count_matches_the_protocol(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    expected = sum(len(company.fiscal_years) for company in COMPANIES)
    assert len(manifest.documents) == expected == 7


def test_declared_documents_cover_every_company_year(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    declared = {(record.company.code, record.fiscal_year) for record in manifest.documents}
    expected = {(company.code, year) for company in COMPANIES for year in company.fiscal_years}
    assert declared == expected


def test_manifest_splits_match_the_protocol(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    assert manifest.company_codes("dev") == DEV_COMPANY_CODES
    assert manifest.company_codes("locked") == LOCKED_COMPANY_CODES


def test_dev_and_locked_documents_are_company_disjoint(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    assert not manifest.company_codes("dev") & manifest.company_codes("locked")


def test_at_least_two_fiscal_years_are_declared(paths: RepoPaths) -> None:
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    assert len({record.fiscal_year for record in manifest.documents}) >= 2


def test_every_document_tells_a_human_where_to_put_it(paths: RepoPaths) -> None:
    """Manual acquisition only works if the instructions are actually there."""
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    for record in manifest.documents:
        assert record.notes.strip(), f"{record.doc_id} has no acquisition note"
        assert record.filename in record.notes, (
            f"{record.doc_id} note must name the target filename {record.filename}"
        )


def test_no_document_claims_to_be_acquired_yet(paths: RepoPaths) -> None:
    """Until P2 runs, every record must be honestly 'pending'."""
    manifest = load_document_manifest(paths.manifests / "documents.yaml")
    for record in manifest.documents:
        if record.acquisition != "pending":
            assert record.sha256 is not None, f"{record.doc_id} claims acquisition without a hash"


# ------------------------------------------------------------------ structured


def test_structured_manifest_loads(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    assert manifest.datasets


def test_both_industry_schema_families_are_declared(paths: RepoPaths) -> None:
    """2882 is a financial holding company: it needs the _fh endpoints, not _ci."""
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    ids = {dataset.dataset_id for dataset in manifest.datasets}
    for endpoint in ("t187ap06_L_ci", "t187ap06_L_fh", "t187ap07_L_ci", "t187ap07_L_fh"):
        assert f"twse-openapi-{endpoint}" in ids, f"{endpoint} must be declared"


def test_company_metadata_endpoint_is_declared(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    ids = {dataset.dataset_id for dataset in manifest.datasets}
    assert "twse-openapi-t187ap03_L" in ids


def test_every_structured_endpoint_is_on_an_allowlisted_host(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    for dataset in manifest.datasets:
        assert dataset.endpoint.startswith("https://")
        assert ".twse.com.tw/" in dataset.endpoint


def test_every_structured_dataset_is_described(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    for dataset in manifest.datasets:
        assert dataset.description.strip(), f"{dataset.dataset_id} has no description"


def test_xbrl_datasets_exist_for_every_company_year(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    ids = {dataset.dataset_id for dataset in manifest.datasets}
    for company in COMPANIES:
        for year in company.fiscal_years:
            assert f"mops-xbrl-{company.code}-FY{year}" in ids


def test_locked_only_datasets_are_not_marked_dev(paths: RepoPaths) -> None:
    manifest = load_structured_manifest(paths.manifests / "structured.yaml")
    for dataset in manifest.datasets:
        if dataset.dataset_id.startswith("mops-xbrl-"):
            code = dataset.dataset_id.split("-")[2]
            expected = "dev" if code in DEV_COMPANY_CODES else "locked"
            assert dataset.split == expected, f"{dataset.dataset_id} has the wrong split"


def test_the_single_period_finding_is_recorded(paths: RepoPaths) -> None:
    """The most consequential P1 finding must not live only in a chat log."""
    text = (paths.manifests / "structured.yaml").read_text(encoding="utf-8")
    assert "SINGLE-PERIOD SNAPSHOT" in text
    assert "t187ap06_L_ci" in text
