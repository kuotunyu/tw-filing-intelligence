"""Turning declarations into a verifiable acquisition record.

Two acquisition modes, one record format:

* **fetched** -- the TWSE OpenAPI, downloaded through :class:`~twfi.io.http.PoliteClient`.
* **manual** -- filings placed by hand, because the only automated paths available
  would require an unpublished API or form simulation (DECISIONS D-010).

Both end up in ``acquisition.lock.yaml`` with a SHA-256, so "reproducible" means
the same thing either way: re-obtain the artifact, re-hash it, compare.

Everything here is pure enough to test offline: the HTTP client, the clock, and
the PDF page counter are all injected.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from twfi.errors import DataAccessError
from twfi.io.hashing import sha256_file
from twfi.io.http import PoliteClient
from twfi.io.manifest import (
    AcquisitionLock,
    AcquisitionRecord,
    DocumentManifest,
    StructuredManifest,
)

__all__ = [
    "ExpectedArtifact",
    "expected_artifacts",
    "count_pdf_pages",
    "fetch_structured_datasets",
    "register_manual_artifacts",
    "provenance_table",
]

Kind = Literal["document", "dataset"]


@dataclass(frozen=True, slots=True)
class ExpectedArtifact:
    """One artifact the study declares, and how a human obtains it."""

    id: str
    kind: Kind
    relative_path: Path
    source_page: str
    hint: str
    required: bool

    def local_path(self, repo_root: Path) -> Path:
        return repo_root / self.relative_path


def expected_artifacts(
    documents: DocumentManifest, structured: StructuredManifest
) -> list[ExpectedArtifact]:
    """Every artifact that must be placed by hand, in a stable order.

    The XBRL bundles are ``required=False``: they improve the study (official
    structured values instead of extracted ones) but the protocol does not depend
    on them, and pretending otherwise would block the whole pipeline on an
    optional input.
    """
    artifacts = [
        ExpectedArtifact(
            id=record.doc_id,
            kind="document",
            relative_path=record.relative_path,
            source_page=record.source_page,
            hint=record.notes,
            required=True,
        )
        for record in documents.documents
    ]
    artifacts += [
        ExpectedArtifact(
            id=dataset.dataset_id,
            kind="dataset",
            relative_path=dataset.relative_path,
            source_page=dataset.endpoint,
            hint=dataset.notes or dataset.description,
            required=False,
        )
        for dataset in structured.manual()
    ]
    return sorted(artifacts, key=lambda artifact: artifact.id)


def count_pdf_pages(path: Path) -> int | None:
    """Return a PDF's page count, or ``None`` if it cannot be read as a PDF."""
    if path.suffix.lower() != ".pdf":
        return None
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - pymupdf is a hard dependency
        return None
    try:
        with pymupdf.open(path) as document:  # type: ignore[no-untyped-call]
            return int(document.page_count)
    except Exception:  # a corrupt file must not abort the whole acquisition run
        return None


def _now() -> str:
    return datetime.now(UTC).isoformat()


def fetch_structured_datasets(
    structured: StructuredManifest,
    client: PoliteClient,
    repo_root: Path,
    lock: AcquisitionLock,
    *,
    now: Callable[[], str] = _now,
    only: Sequence[str] | None = None,
) -> tuple[AcquisitionLock, list[str]]:
    """Download every automated dataset and record it.

    A dataset that fails is reported and skipped -- one unavailable endpoint must
    not discard the records of the ones that succeeded.
    """
    messages: list[str] = []
    for dataset in structured.automated():
        if only is not None and dataset.dataset_id not in only:
            continue
        target = dataset.local_path(repo_root)
        try:
            result = client.download(dataset.endpoint, target)
        except DataAccessError as exc:
            messages.append(f"FAILED {dataset.dataset_id}: {exc}")
            continue

        rows: int | None = None
        try:
            payload = json.loads(target.read_bytes())
        except (UnicodeDecodeError, json.JSONDecodeError):
            messages.append(f"WARN   {dataset.dataset_id}: response is not JSON")
        else:
            if isinstance(payload, list):
                rows = len(payload)

        lock = lock.upsert(
            AcquisitionRecord(
                id=dataset.dataset_id,
                kind="dataset",
                acquisition="fetched",
                relative_path=dataset.relative_path.as_posix(),
                sha256=result.sha256,
                bytes=result.num_bytes,
                retrieved_at=result.retrieved_at or now(),
                source_url=dataset.endpoint,
                http_status=result.status_code,
                rows=rows,
                notes=dataset.description,
            )
        )
        messages.append(
            f"OK     {dataset.dataset_id}: {result.num_bytes} bytes"
            + (f", {rows} rows" if rows is not None else "")
        )
    return lock, messages


def register_manual_artifacts(
    artifacts: Iterable[ExpectedArtifact],
    repo_root: Path,
    lock: AcquisitionLock,
    *,
    now: Callable[[], str] = _now,
    page_counter: Callable[[Path], int | None] = count_pdf_pages,
) -> tuple[AcquisitionLock, list[str], list[ExpectedArtifact]]:
    """Hash whatever has been placed by hand; report what is still missing.

    Returns the updated lock, human-readable messages, and the artifacts that are
    still absent, so a caller can print precise instructions rather than a generic
    "data missing" error.
    """
    messages: list[str] = []
    missing: list[ExpectedArtifact] = []

    for artifact in artifacts:
        target = artifact.local_path(repo_root)
        if not target.is_file():
            missing.append(artifact)
            continue

        digest = sha256_file(target)
        size = target.stat().st_size
        previous = lock.get(artifact.id)
        if previous is not None and previous.sha256 == digest:
            messages.append(f"OK     {artifact.id}: unchanged ({size} bytes)")
            continue
        if previous is not None:
            messages.append(
                f"CHANGED {artifact.id}: digest differs from the previous record "
                f"({previous.sha256[:12]}… -> {digest[:12]}…); the lock now describes the new file"
            )

        lock = lock.upsert(
            AcquisitionRecord(
                id=artifact.id,
                kind=artifact.kind,
                acquisition="manual",
                relative_path=artifact.relative_path.as_posix(),
                sha256=digest,
                bytes=size,
                retrieved_at=now(),
                source_url=None,
                pages=page_counter(target),
                notes=f"placed by hand from {artifact.source_page}",
            )
        )
        messages.append(f"ADDED  {artifact.id}: {size} bytes")

    return lock, messages, missing


def provenance_table(
    documents: DocumentManifest, structured: StructuredManifest, lock: AcquisitionLock
) -> str:
    """Render a human-readable provenance table from the declarations plus the lock."""
    lines = [
        "# Provenance table",
        "",
        "> Generated by `scripts/verify_manifests.py`. Do not edit by hand.",
        "",
        "## Documents",
        "",
        "| doc_id | 公司 | 年度 | 類型 | split | pages | SHA-256 | 取得日 | 方式 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for record in documents.documents:
        acquired = lock.get(record.doc_id)
        digest = f"`{acquired.sha256[:16]}…`" if acquired else "—"
        stamp = acquired.retrieved_at[:10] if acquired else "—"
        mode: str = acquired.acquisition if acquired else "**not acquired**"
        pages = str(acquired.pages) if acquired and acquired.pages else "—"
        lines.append(
            f"| `{record.doc_id}` | {record.company.name} ({record.company.code}) "
            f"| FY{record.fiscal_year} | {record.doc_type} | {record.split} "
            f"| {pages} | {digest} | {stamp} | {mode} |"
        )

    lines += [
        "",
        "## Structured datasets",
        "",
        "| dataset_id | source | split | rows | SHA-256 | 取得日 | 方式 |",
        "|---|---|---|---|---|---|---|",
    ]
    for dataset in structured.datasets:
        acquired = lock.get(dataset.dataset_id)
        digest = f"`{acquired.sha256[:16]}…`" if acquired else "—"
        stamp = acquired.retrieved_at[:10] if acquired else "—"
        dataset_mode: str = acquired.acquisition if acquired else "not acquired"
        rows = str(acquired.rows) if acquired and acquired.rows is not None else "—"
        lines.append(
            f"| `{dataset.dataset_id}` | {dataset.source} | {dataset.split} "
            f"| {rows} | {digest} | {stamp} | {dataset_mode} |"
        )

    lines.append("")
    return "\n".join(lines)
