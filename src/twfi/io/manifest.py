"""Typed manifests: the only record of where data came from.

Original filings are never committed, so these manifests *are* the provenance.
That makes their invariants load-bearing rather than cosmetic:

* Every URL must satisfy the same allowlist the HTTP client enforces, so a
  manifest cannot smuggle in an off-allowlist target.
* A record's ``split`` must match the split the protocol assigned to that
  company, so the dev/locked separation cannot drift via the manifest.
* A record is either fully unfetched or fully accounted for -- half-recorded
  states (a hash with no timestamp, a timestamp with no hash) are rejected,
  because those are what make a "reproducible" pipeline unreproducible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from twfi.errors import DisallowedHostError, HashMismatchError, ManifestError
from twfi.io.hashing import sha256_file
from twfi.io.http import assert_url_allowed
from twfi.protocol import DocType, split_for_company

__all__ = [
    "CompanyRef",
    "DocumentRecord",
    "DocumentManifest",
    "StructuredDataset",
    "StructuredManifest",
    "load_document_manifest",
    "load_structured_manifest",
    "dump_manifest",
    "verify_local_documents",
    "assert_local_documents_match",
]

Acquisition = Literal["pending", "fetched", "manual"]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

_SUFFIX_BY_DOC_TYPE: dict[str, str] = {
    "annual_report": ".pdf",
    "financial_report": ".pdf",
    "xbrl": ".zip",
}


def _validate_url_field(url: str | None, field_name: str) -> str | None:
    if url is None:
        return None
    try:
        assert_url_allowed(url)
    except DisallowedHostError as exc:
        raise ValueError(f"{field_name}: {exc}") from exc
    return url


class CompanyRef(BaseModel):
    """A company as named in a manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    code: str = Field(pattern=r"^[0-9]{4}$")


class DocumentRecord(BaseModel):
    """One filing: what it is, where it came from, and whether we have it."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(pattern=r"^[0-9]{4}-FY[0-9]{4}-[A-Z0-9]+$")
    company: CompanyRef
    fiscal_year: int = Field(ge=2000, le=2100)
    doc_type: DocType
    split: Literal["dev", "locked"]
    source_page: str
    resolved_url: str | None = None
    sha256: Sha256 | None = None
    bytes: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=1)
    retrieved_at: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    acquisition: Acquisition = "pending"
    notes: str = ""

    @property
    def filename(self) -> str:
        return f"{self.doc_id}{_SUFFIX_BY_DOC_TYPE[self.doc_type]}"

    def local_path(self, raw_root: Path) -> Path:
        """Where this document lives once fetched (never committed)."""
        return raw_root / self.doc_type / self.filename

    @model_validator(mode="after")
    def _check(self) -> Self:
        _validate_url_field(self.source_page, "source_page")
        _validate_url_field(self.resolved_url, "resolved_url")

        expected_prefix = f"{self.company.code}-FY{self.fiscal_year}-"
        if not self.doc_id.startswith(expected_prefix):
            raise ValueError(f"doc_id {self.doc_id!r} must start with {expected_prefix!r}")

        try:
            protocol_split = split_for_company(self.company.code)
        except KeyError:
            raise ValueError(
                f"company {self.company.code} is not part of the pre-registered study; "
                "amend docs/FEASIBILITY_PROTOCOL.md before adding it"
            ) from None
        if self.split != protocol_split:
            raise ValueError(
                f"company {self.company.code} is assigned to the {protocol_split!r} split "
                f"by the protocol, but this record says {self.split!r}"
            )

        if self.acquisition == "pending":
            populated = [
                name
                for name in ("resolved_url", "sha256", "bytes", "retrieved_at", "http_status")
                if getattr(self, name) is not None
            ]
            if populated:
                raise ValueError(
                    f"acquisition is 'pending' but these fields are already set: {populated}; "
                    "a half-recorded document is not reproducible"
                )
            return self

        required = ["sha256", "bytes", "retrieved_at"]
        if self.acquisition == "fetched":
            required += ["resolved_url", "http_status"]
        missing = [name for name in required if getattr(self, name) is None]
        if missing:
            raise ValueError(f"acquisition is {self.acquisition!r} but {missing} are missing")
        return self


class DocumentManifest(BaseModel):
    """The declared set of filings."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    documents: list[DocumentRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [record.doc_id for record in self.documents]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate doc_id: {duplicates}")
        return self

    def by_split(self, split: Literal["dev", "locked"]) -> list[DocumentRecord]:
        return [record for record in self.documents if record.split == split]

    def company_codes(self, split: Literal["dev", "locked"]) -> set[str]:
        return {record.company.code for record in self.by_split(split)}


class StructuredDataset(BaseModel):
    """One structured dataset (TWSE OpenAPI endpoint or XBRL bundle)."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    source: Literal["S1", "S2", "S3"]
    endpoint: str
    description: str = ""
    split: Literal["dev", "locked", "both"] = "both"
    sha256: Sha256 | None = None
    rows: int | None = Field(default=None, ge=0)
    bytes: int | None = Field(default=None, ge=0)
    retrieved_at: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    acquisition: Acquisition = "pending"
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        _validate_url_field(self.endpoint, "endpoint")
        if self.acquisition == "pending":
            return self
        missing = [
            name for name in ("sha256", "retrieved_at", "bytes") if getattr(self, name) is None
        ]
        if missing:
            raise ValueError(f"acquisition is {self.acquisition!r} but {missing} are missing")
        return self


class StructuredManifest(BaseModel):
    """The declared set of structured datasets."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    datasets: list[StructuredDataset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        ids = [dataset.dataset_id for dataset in self.datasets]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate dataset_id: {duplicates}")
        return self


def _load_yaml(path: Path) -> object:
    if not path.is_file():
        raise ManifestError(f"no manifest at {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ManifestError(f"{path} is not valid YAML: {exc}") from exc


def load_document_manifest(path: Path) -> DocumentManifest:
    """Load and validate ``documents.yaml``.

    Raises:
        ManifestError: If the file is missing, malformed, or violates an invariant.
    """
    payload = _load_yaml(path)
    try:
        return DocumentManifest.model_validate(payload)
    except ValidationError as exc:
        raise ManifestError(f"{path} is not a valid document manifest:\n{exc}") from exc


def load_structured_manifest(path: Path) -> StructuredManifest:
    """Load and validate ``structured.yaml``.

    Raises:
        ManifestError: If the file is missing, malformed, or violates an invariant.
    """
    payload = _load_yaml(path)
    try:
        return StructuredManifest.model_validate(payload)
    except ValidationError as exc:
        raise ManifestError(f"{path} is not a valid structured manifest:\n{exc}") from exc


def dump_manifest(manifest: DocumentManifest | StructuredManifest, path: Path) -> None:
    """Write a manifest back to YAML with stable field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = manifest.model_dump(mode="json")
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )


def verify_local_documents(manifest: DocumentManifest, raw_root: Path) -> list[str]:
    """Re-hash every acquired document and report mismatches.

    Returns a list of human-readable problems; empty means the local copies match
    the manifest exactly. ``pending`` records are skipped, not reported.
    """
    problems: list[str] = []
    for record in manifest.documents:
        if record.acquisition == "pending":
            continue
        target = record.local_path(raw_root)
        if not target.is_file():
            problems.append(
                f"{record.doc_id}: manifest says {record.acquisition} but {target} is missing "
                "(re-run scripts/fetch_documents.py, or place the file manually)"
            )
            continue
        actual = sha256_file(target)
        if actual != record.sha256:
            problems.append(
                f"{record.doc_id}: sha256 mismatch (manifest {record.sha256}, file {actual})"
            )
            continue
        size = target.stat().st_size
        if record.bytes is not None and size != record.bytes:
            problems.append(
                f"{record.doc_id}: size mismatch (manifest {record.bytes}, file {size})"
            )
    return problems


def assert_local_documents_match(manifest: DocumentManifest, raw_root: Path) -> None:
    """Raise if any acquired document does not match the manifest.

    Raises:
        HashMismatchError: With every problem listed at once.
    """
    problems = verify_local_documents(manifest, raw_root)
    if problems:
        raise HashMismatchError(
            "local documents do not match the manifest:\n  - " + "\n  - ".join(problems)
        )
