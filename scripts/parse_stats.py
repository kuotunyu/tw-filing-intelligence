"""Run both parsers over every acquired filing and record what each recovered.

    uv run python scripts/parse_stats.py

Writes results/runs/parse_stats.json and prints a comparison. This is the P3
definition-of-done artifact, and the first look at whether the structure-aware
parser recovers anything on real filings rather than only on synthetic fixtures.

CPU only. No models, no GPU, no network.
"""

from __future__ import annotations

import json

import typer

from twfi.io.manifest import load_acquisition_lock, load_document_manifest
from twfi.parsing.stats import compare_parsers
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)


@app.command()
def main() -> None:
    """Parse every acquired document with both parsers and report the difference."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    manifest = load_document_manifest(paths.documents_manifest)
    lock = load_acquisition_lock(paths.acquisition_lock)

    comparisons = []
    for record in manifest.documents:
        target = record.local_path(paths.root)
        if lock.get(record.doc_id) is None or not target.is_file():
            typer.echo(f"skip  {record.doc_id}: not acquired yet")
            continue
        typer.echo(f"parse {record.doc_id} …")
        comparisons.append(compare_parsers(target, record.doc_id))

    if not comparisons:
        typer.echo("nothing to parse; run scripts/fetch_documents.py first")
        raise typer.Exit(code=1)

    header = (
        f"{'doc_id':<18}{'pages':>6}{'F0 s':>8}{'F1 s':>8}"
        f"{'head':>7}{'para':>7}{'furn':>6}{'F0 chk':>8}{'F1 chk':>8}{'sect%':>7}{'xpage':>7}"
    )
    typer.echo("")
    typer.echo(header)
    typer.echo("-" * len(header))
    for item in comparisons:
        candidate = item.candidate
        section_pct = (
            100.0 * candidate.chunks.with_section_path / candidate.chunks.count
            if candidate.chunks.count
            else 0.0
        )
        typer.echo(
            f"{item.doc_id:<18}{item.pages:>6}"
            f"{item.baseline.parse_seconds:>8.2f}{candidate.parse_seconds:>8.2f}"
            f"{candidate.blocks.get('heading', 0):>7}{candidate.blocks.get('paragraph', 0):>7}"
            f"{candidate.blocks.get('header_footer', 0):>6}"
            f"{item.baseline.chunks.count:>8}{candidate.chunks.count:>8}"
            f"{section_pct:>6.0f}%{candidate.chunks.cross_page:>7}"
        )

    total_pages = sum(item.pages for item in comparisons)
    baseline_seconds = sum(item.baseline.parse_seconds for item in comparisons)
    candidate_seconds = sum(item.candidate.parse_seconds for item in comparisons)

    payload = {
        "documents": [item.to_json() for item in comparisons],
        "totals": {
            "documents": len(comparisons),
            "pages": total_pages,
            "baseline_parse_seconds": round(baseline_seconds, 2),
            "candidate_parse_seconds": round(candidate_seconds, 2),
            "baseline_seconds_per_page": round(baseline_seconds / total_pages, 4)
            if total_pages
            else 0.0,
            "candidate_seconds_per_page": round(candidate_seconds / total_pages, 4)
            if total_pages
            else 0.0,
        },
    }

    target = paths.runs / "parse_stats.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    typer.echo("")
    typer.echo(f"documents : {len(comparisons)}")
    typer.echo(f"pages     : {total_pages}")
    per_page_baseline = baseline_seconds / total_pages
    per_page_candidate = candidate_seconds / total_pages
    typer.echo(f"F0 parse  : {baseline_seconds:.1f}s ({per_page_baseline:.3f}s/page)")
    typer.echo(f"F1 parse  : {candidate_seconds:.1f}s ({per_page_candidate:.3f}s/page)")
    typer.echo(f"wrote     : {target.relative_to(paths.root)}")


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
