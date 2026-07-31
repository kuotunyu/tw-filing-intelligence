"""Build an annotation worklist: where the evidence is, never what it says.

    uv run python scripts/make_worklist.py --for probe
    uv run python scripts/make_worklist.py --for probe --company 2330

Writes `data/evaluation/worklist/<kind>.jsonl`, which holds `DraftItem` records. A
draft has no answer field, so this script cannot produce a gold answer even by mistake;
promoting a slot into `gold.jsonl` means a person opened the page and wrote the answer.

Probes (gate G8) run with retrieval forced empty, so a probe only tests anything if the
model plausibly memorised the figure during pretraining. The topics are therefore the
headline figures of the locked issuers -- see `twfi.eval.worklist` for why.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Annotated, Any

import typer

from twfi.errors import ParsingError
from twfi.eval.gold import CompanyRef, DraftItem
from twfi.eval.worklist import probe_slots
from twfi.io.manifest import load_acquisition_lock, load_document_manifest
from twfi.parsing.baseline import parse_baseline
from twfi.paths import repo_paths
from twfi.protocol import COMPANIES, USABLE_DOCUMENTS

app = typer.Typer(add_completion=False, help=__doc__)

_NAME_BY_CODE = {company.code: company.name for company in COMPANIES}


@app.command()
def main(
    for_kind: Annotated[
        str, typer.Option("--for", help="Which worklist to build. Currently: probe.")
    ] = "probe",
    company: Annotated[
        str | None, typer.Option(help="Restrict to one company code, e.g. 2330.")
    ] = None,
    split: Annotated[
        str, typer.Option(help="Which side to draw on: locked, dev, or both.")
    ] = "locked",
) -> None:
    """Locate evidence for annotation slots and write them to the worklist."""
    if for_kind != "probe":
        typer.echo(f"unknown worklist kind {for_kind!r}; only 'probe' is implemented")
        raise typer.Exit(code=2)

    paths = repo_paths()
    lock = load_acquisition_lock(paths.acquisition_lock)
    manifest = load_document_manifest(paths.documents_manifest)
    # Documents are placed by hand, so the recorded provenance is the MOPS search page
    # the filing came from, not a direct file URL.
    urls = {entry.doc_id: entry.source_page for entry in manifest.documents}

    wanted = [
        document
        for document in USABLE_DOCUMENTS
        if (split == "both" or document.split == split)
        and (company is None or document.company_code == company)
    ]
    if not wanted:
        typer.echo(f"no usable documents match split={split} company={company}")
        raise typer.Exit(code=2)

    slots: list[DraftItem] = []
    for document in wanted:
        record = lock.get(document.doc_id)
        if record is None:
            typer.echo(f"{document.doc_id:<18} not acquired -- skipped")
            continue
        pdf_path = record.local_path(paths.root)
        if not pdf_path.is_file():
            typer.echo(f"{document.doc_id:<18} {record.relative_path} missing -- skipped")
            continue

        try:
            parsed = parse_baseline(pdf_path, document.doc_id)
        except ParsingError as exc:
            typer.echo(f"{document.doc_id:<18} unreadable: {exc}")
            continue

        pages = _page_texts(parsed)
        found = probe_slots(
            doc_id=document.doc_id,
            company=CompanyRef(_NAME_BY_CODE[document.company_code], document.company_code),
            period=f"FY{document.fiscal_year}",
            pages=pages,
            source_url=urls.get(document.doc_id),
        )
        slots.extend(found)
        typer.echo(f"{document.doc_id:<18} {len(pages):>4} pages  {len(found)} slot(s)")
        for slot in found:
            typer.echo(f"    {slot.draft_id:<40} pages {list(slot.page_numbers)}")

    target = paths.worklist / f"{for_kind}.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "".join(json.dumps(_as_json(slot), ensure_ascii=False) + "\n" for slot in slots),
        encoding="utf-8",
    )

    typer.echo("")
    typer.echo(f"wrote {len(slots)} draft slot(s): {target.relative_to(paths.root)}")
    typer.echo("")
    typer.echo("These are slots, not answers. For each one you keep: open the cited page,")
    typer.echo("read the figure, write the question and the answer yourself, then add the")
    typer.echo(f"record to {paths.locked_probes.relative_to(paths.root)} with")
    typer.echo('  "annotator": "human", "answer_provenance": "human_read_pdf"')
    typer.echo("and validate with: uv run python scripts/validate_gold.py --set probe")


def _page_texts(parsed: Any) -> list[str]:
    """One string per page, in page order, with gaps preserved as empty strings."""
    by_page: dict[int, list[str]] = {}
    for block in parsed.blocks:
        by_page.setdefault(block.page, []).append(block.text)
    if not by_page:
        return []
    return ["\n".join(by_page.get(page, ())) for page in range(1, max(by_page) + 1)]


def _as_json(slot: DraftItem) -> dict[str, Any]:
    payload = dataclasses.asdict(slot)
    payload["company"] = {"name": slot.company.name, "code": slot.company.code}
    payload["evidence_hint"] = [{"kind": item.kind, "ref": item.ref} for item in slot.evidence_hint]
    payload["bbox"] = [{"page": ref.page, "bbox": list(ref.bbox)} for ref in slot.bbox]
    return payload


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
