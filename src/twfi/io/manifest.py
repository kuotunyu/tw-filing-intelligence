"""Typed manifests: the only record of where data came from.

Original filings are never committed, so these files *are* the provenance. They
are split by writer, which is the point:

* ``data/manifests/documents.yaml`` and ``structured.yaml`` are **declarations**,
  hand-authored. They say what the study uses and where a human obtains it, and
  they carry the reasoning in comments. No script rewrites them.
* ``data/manifests/acquisition.lock.yaml`` is the **record**, machine-written.
  It says what was actually obtained: digest, size, timestamp, local path.

Keeping them apart means a fetch cannot silently erase the reasoning, and an
:class:`AcquisitionRecord` cannot represent a half-recorded state at all -- the
fields that make a download verifiable are simply not optional.

Invariants worth stating, because they are load-bearing rather than cosmetic:

* Every URL must satisfy the same allowlist the HTTP client enforces, so a
  manifest cannot smuggle in an off-allowlist target.
* A record's ``split`` must match the split the protocol assigned to that
  company, so the dev/locked separation cannot drift via the manifest.
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
    "DOCUMENT_DIR",
    "STRUCTURED_DIR",
    "LOCK_HEADER",
    "CompanyRef",
    "DocumentRecord",
    "DocumentManifest",
    "StructuredDataset",
    "StructuredManifest",
    "AcquisitionRecord",
    "AcquisitionLock",
    "load_document_manifest",
    "load_structured_manifest",
    "load_acquisition_lock",
    "dump_yaml_model",
    "verify_acquisition",
    "assert_acquisition_matches",
]

#: Where acquired artifacts live. Both are git-ignored.
#: Documents are placed by hand (see DECISIONS D-010), which is why the directory
#: is named for the acquisition mode rather than for the content.
DOCUMENT_DIR = Path("data/raw/manual")
STRUCTURED_DIR = Path("data/raw/structured")

#: Prepended to ``acquisition.lock.yaml`` so the file states its own provenance
#: rules to anyone who opens it.
LOCK_HEADER = """GENERATED FILE -- do not edit by hand.

Written by scripts/fetch_twse_openapi.py and scripts/fetch_documents.py.
Declarations live in documents.yaml and structured.yaml; this file records what
was actually obtained. Frozen by scripts/freeze_protocol.py, so an edit here
after the freeze fails the test suite."""

Acquisition = Literal["fetched", "manual"]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]

_SUFFIX_BY_DOC_TYPE: dict[str, str] = {
    "annual_report": ".pdf",
    "financial_report": ".pdf",
    "xbrl": ".zip",
}


def _validate_url_field(url: str, field_name: str) -> None:
    try:
        assert_url_allowed(url)
    except DisallowedHostError as exc:
        raise ValueError(f"{field_name}: {exc}") from exc


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


# --------------------------------------------------------------- declarations


class CompanyRef(BaseModel):
    """A company as named in a manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    code: str = Field(pattern=r"^[0-9]{4}$")


class DocumentRecord(BaseModel):
    """A declared filing: what it is and where a human obtains it."""

    model_config = ConfigDict(extra="forbid")

    doc_id: str = Field(pattern=r"^[0-9]{4}-FY[0-9]{4}-[A-Z0-9]+$")
    company: CompanyRef
    fiscal_year: int = Field(ge=2000, le=2100)
    doc_type: DocType
    split: Literal["dev", "locked"]
    source_page: str
    notes: str = ""

    @property
    def filename(self) -> str:
        return f"{self.doc_id}{_SUFFIX_BY_DOC_TYPE[self.doc_type]}"

    @property
    def relative_path(self) -> Path:
        return DOCUMENT_DIR / self.filename

    def local_path(self, repo_root: Path) -> Path:
        return repo_root / self.relative_path

    @model_validator(mode="after")
    def _check(self) -> Self:
        _validate_url_field(self.source_page, "source_page")

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
        return self


class DocumentManifest(BaseModel):
    """The declared set of filings."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    documents: list[DocumentRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        duplicates = _duplicates([record.doc_id for record in self.documents])
        if duplicates:
            raise ValueError(f"duplicate doc_id: {duplicates}")
        return self

    def by_split(self, split: Literal["dev", "locked"]) -> list[DocumentRecord]:
        return [record for record in self.documents if record.split == split]

    def company_codes(self, split: Literal["dev", "locked"]) -> set[str]:
        return {record.company.code for record in self.by_split(split)}

    def get(self, doc_id: str) -> DocumentRecord:
        """Return one declared document.

        Raises:
            KeyError: If the document is not declared.
        """
        for record in self.documents:
            if record.doc_id == doc_id:
                return record
        raise KeyError(doc_id)


class StructuredDataset(BaseModel):
    """A declared structured dataset (TWSE OpenAPI endpoint or MOPS XBRL bundle)."""

    model_config = ConfigDict(extra="forbid")

    dataset_id: str = Field(min_length=1)
    source: Literal["S1", "S2", "S3"]
    endpoint: str
    description: str = ""
    split: Literal["dev", "locked", "both"] = "both"
    notes: str = ""

    @property
    def automated(self) -> bool:
        """True for the TWSE OpenAPI, which is fetchable politely and directly."""
        return self.source == "S3"

    @property
    def relative_path(self) -> Path:
        if self.automated:
            return STRUCTURED_DIR / f"{self.dataset_id}.json"
        return DOCUMENT_DIR / f"{self.dataset_id}.zip"

    def local_path(self, repo_root: Path) -> Path:
        return repo_root / self.relative_path

    @model_validator(mode="after")
    def _check(self) -> Self:
        _validate_url_field(self.endpoint, "endpoint")
        return self


class StructuredManifest(BaseModel):
    """The declared set of structured datasets."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    datasets: list[StructuredDataset] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        duplicates = _duplicates([dataset.dataset_id for dataset in self.datasets])
        if duplicates:
            raise ValueError(f"duplicate dataset_id: {duplicates}")
        return self

    def automated(self) -> list[StructuredDataset]:
        return [dataset for dataset in self.datasets if dataset.automated]

    def manual(self) -> list[StructuredDataset]:
        return [dataset for dataset in self.datasets if not dataset.automated]


# ------------------------------------------------------------ acquisition record


class AcquisitionRecord(BaseModel):
    """Proof that one artifact was obtained, with everything needed to re-verify it.

    Every field required to check the artifact is mandatory. A record that cannot
    be verified cannot be written, which is the property the manifest schema was
    previously enforcing with a validator.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["document", "dataset"]
    acquisition: Acquisition
    relative_path: str = Field(min_length=1)
    sha256: Sha256
    bytes: int = Field(ge=0)
    retrieved_at: str = Field(min_length=1)
    source_url: str | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    rows: int | None = Field(default=None, ge=0)
    pages: int | None = Field(default=None, ge=1)
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.source_url is not None:
            _validate_url_field(self.source_url, "source_url")
        if self.acquisition == "fetched" and (self.source_url is None or self.http_status is None):
            raise ValueError(
                f"{self.id}: an automated fetch must record source_url and http_status"
            )
        return self

    def local_path(self, repo_root: Path) -> Path:
        return repo_root / self.relative_path


class AcquisitionLock(BaseModel):
    """Everything acquired so far, machine-written."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    records: list[AcquisitionRecord] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Self:
        duplicates = _duplicates([record.id for record in self.records])
        if duplicates:
            raise ValueError(f"duplicate acquisition id: {duplicates}")
        return self

    @property
    def ids(self) -> set[str]:
        return {record.id for record in self.records}

    def get(self, record_id: str) -> AcquisitionRecord | None:
        for record in self.records:
            if record.id == record_id:
                return record
        return None

    def upsert(self, record: AcquisitionRecord) -> AcquisitionLock:
        """Return a new lock with ``record`` replacing any record of the same id.

        Order is kept stable by id so the file's diff stays readable and its hash
        does not change just because a fetch ran in a different sequence.
        """
        kept = [existing for existing in self.records if existing.id != record.id]
        return AcquisitionLock(
            version=self.version,
            records=sorted([*kept, record], key=lambda item: item.id),
        )


# ---------------------------------------------------------------------- loading


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
    try:
        return DocumentManifest.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ManifestError(f"{path} is not a valid document manifest:\n{exc}") from exc


def load_structured_manifest(path: Path) -> StructuredManifest:
    """Load and validate ``structured.yaml``.

    Raises:
        ManifestError: If the file is missing, malformed, or violates an invariant.
    """
    try:
        return StructuredManifest.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ManifestError(f"{path} is not a valid structured manifest:\n{exc}") from exc


def load_acquisition_lock(path: Path) -> AcquisitionLock:
    """Load ``acquisition.lock.yaml``, treating absence as "nothing acquired yet".

    Raises:
        ManifestError: If the file exists but is malformed.
    """
    if not path.is_file():
        return AcquisitionLock()
    try:
        return AcquisitionLock.model_validate(_load_yaml(path))
    except ValidationError as exc:
        raise ManifestError(f"{path} is not a valid acquisition lock:\n{exc}") from exc


def dump_yaml_model(model: BaseModel, path: Path, *, header: str = "") -> None:
    """Write a pydantic model to YAML with stable field order."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(
        model.model_dump(mode="json"), sort_keys=False, allow_unicode=True, width=100
    )
    prefix = "".join(f"# {line}\n" for line in header.splitlines()) if header else ""
    path.write_text(prefix + body, encoding="utf-8")


# ------------------------------------------------------------------ verification


def verify_acquisition(
    lock: AcquisitionLock,
    repo_root: Path,
    *,
    expected_ids: set[str] | None = None,
) -> list[str]:
    """Re-hash every acquired artifact and report mismatches.

    Returns human-readable problems; empty means every recorded artifact is present
    and byte-identical. ``expected_ids`` additionally reports what has not been
    acquired yet, which is how G1 evidence is assembled.
    """
    problems: list[str] = []
    for record in lock.records:
        target = record.local_path(repo_root)
        if not target.is_file():
            problems.append(
                f"{record.id}: recorded as {record.acquisition} but {record.relative_path} "
                "is missing (re-fetch, or place the file again)"
            )
            continue
        actual = sha256_file(target)
        if actual != record.sha256:
            problems.append(f"{record.id}: sha256 mismatch (lock {record.sha256}, file {actual})")
            continue
        size = target.stat().st_size
        if size != record.bytes:
            problems.append(f"{record.id}: size mismatch (lock {record.bytes}, file {size})")

    if expected_ids is not None:
        for missing in sorted(expected_ids - lock.ids):
            problems.append(f"{missing}: declared but not acquired yet")
    return problems


def assert_acquisition_matches(
    lock: AcquisitionLock,
    repo_root: Path,
    *,
    expected_ids: set[str] | None = None,
) -> None:
    """Raise if any acquired artifact does not match the lock.

    Raises:
        HashMismatchError: With every problem listed at once.
    """
    problems = verify_acquisition(lock, repo_root, expected_ids=expected_ids)
    if problems:
        raise HashMismatchError(
            "acquired data does not match the lock:\n  - " + "\n  - ".join(problems)
        )
