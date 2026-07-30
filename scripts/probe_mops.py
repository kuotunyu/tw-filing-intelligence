"""Probe whether MOPS document paths are usable politely, and record the answer.

Gate G1 requires the data acquisition to be reproducible *without* breaking
CAPTCHAs, hammering MOPS, or using private endpoints. That is an empirical
question about a specific set of public pages, so this script asks it in a
bounded way: a handful of GETs, no form simulation, no retries beyond the shared
politeness budget, and a report of what came back.

    uv run python scripts/probe_mops.py

Whatever it finds -- usable or not -- goes into ``docs/DATA_PROVENANCE.md``. A
negative result is a valid and useful outcome: it justifies the manual-placement
fallback instead of escalating to techniques the protocol forbids.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import typer

from twfi.errors import DataAccessError
from twfi.io.http import PoliteClient
from twfi.paths import repo_paths

app = typer.Typer(add_completion=False, help=__doc__)

#: Public entry points, in the order a human would reach them. Nothing here is
#: reverse-engineered: these are the pages MOPS itself links to.
PROBES: tuple[tuple[str, str], ...] = (
    ("mops-home", "https://mops.twse.com.tw/mops/web/index"),
    ("mops-annual-report-search", "https://mops.twse.com.tw/mops/web/t57sb01_q1"),
    ("doc-file-search", "https://doc.twse.com.tw/server-java/t57sb01"),
)

_PDF_LINK = re.compile(r"""href=["']?([^"'>\s]+\.pdf)""", re.IGNORECASE)
_FORM_ACTION = re.compile(r"""<form[^>]*action=["']?([^"'>\s]+)""", re.IGNORECASE)
_SPA_MARKERS = ('<div id="app"', "__NUXT__", "window.__INITIAL", '<script type="module"')
_CAPTCHA_MARKERS = ("captcha", "驗證碼", "圖形驗證")


@dataclass(slots=True)
class ProbeOutcome:
    """What one public page actually returned."""

    name: str
    url: str
    status: int | None = None
    num_bytes: int | None = None
    content_type: str = ""
    looks_like_spa: bool = False
    mentions_captcha: bool = False
    pdf_links: list[str] = field(default_factory=list)
    redirects: tuple[str, ...] = ()
    forms: list[str] = field(default_factory=list)
    excerpt: str = ""
    error: str = ""

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "url": self.url,
            "status": self.status,
            "bytes": self.num_bytes,
            "content_type": self.content_type,
            "looks_like_spa": self.looks_like_spa,
            "mentions_captcha": self.mentions_captcha,
            "pdf_links": self.pdf_links[:10],
            "redirects": list(self.redirects),
            "forms": self.forms[:10],
            "excerpt": self.excerpt,
            "error": self.error,
        }


def _decode(payload: bytes) -> str:
    """MOPS has historically served Big5; try the encodings it plausibly uses."""
    for encoding in ("utf-8", "big5hkscs", "cp950"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def probe_all(client: PoliteClient, body_dir: Path | None = None) -> list[ProbeOutcome]:
    """Fetch each public entry point once and summarise it.

    Bodies are saved (git-ignored) so follow-up analysis does not mean re-fetching:
    the politeness budget is a shared resource, not a per-question allowance.
    """
    outcomes: list[ProbeOutcome] = []
    for name, url in PROBES:
        outcome = ProbeOutcome(name=name, url=url)
        try:
            payload, result = client.get_bytes(url)
        except DataAccessError as exc:
            outcome.error = str(exc)
        else:
            text = _decode(payload)
            lowered = text.lower()
            outcome.status = result.status_code
            outcome.num_bytes = result.num_bytes
            outcome.redirects = result.redirects
            outcome.looks_like_spa = any(marker.lower() in lowered for marker in _SPA_MARKERS)
            outcome.mentions_captcha = any(marker in lowered for marker in _CAPTCHA_MARKERS)
            outcome.pdf_links = sorted(set(_PDF_LINK.findall(text)))
            outcome.forms = sorted(set(_FORM_ACTION.findall(text)))
            outcome.excerpt = " ".join(text.split())[:600]
            if body_dir is not None:
                body_dir.mkdir(parents=True, exist_ok=True)
                (body_dir / f"{name}.html").write_text(text, encoding="utf-8")
        outcomes.append(outcome)
    return outcomes


@app.command()
def main(
    save: Annotated[bool, typer.Option(help="Write the report under results/runs/.")] = True,
) -> None:
    """Probe the public MOPS entry points and print a machine-readable report."""
    paths = repo_paths()
    paths.ensure_generated_dirs()

    with PoliteClient() as client:
        outcomes = probe_all(client, body_dir=paths.runs / "mops_probe_bodies")
        network = client.snapshot()

    report = {
        "probes": [outcome.to_json() for outcome in outcomes],
        "network": network,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    typer.echo(rendered)

    if save:
        target = paths.runs / "mops_probe.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        typer.echo(f"\nsaved: {target}")


def _entrypoint() -> None:
    app()


if __name__ == "__main__":
    _entrypoint()
