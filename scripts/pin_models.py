"""Pin the models to what is actually installed, and fail if that is not what was declared.

    uv run python scripts/pin_models.py
    uv run python scripts/pin_models.py --dry-run

Reads `configs/models.yaml`, observes what is installed, and writes
`configs/models.lock.json` for the protocol lock to hash.

The point is the refusal. If a declared digest does not match the installed one, this **fails**
rather than writing the observed value into the lock. Silently updating the lock to match
reality is the one behaviour that would make pinning meaningless: the study would always be
"running the pinned model", whatever model that happened to be. Protocol 2.2 says models may
not be swapped because results were poor, and a lock that rewrites itself is exactly how that
rule gets broken without anyone deciding to break it.

No inference and no GPU. `ollama show` reads manifest metadata and does not load weights, and
Hugging Face revisions come from the local cache directory rather than the network -- so this
is safe to run while another project has the card, and it works offline.

The chart challenger is recorded as cancelled, not as pending. D-021: the dev filings contain
no charts, so the comparison has no material and will not run. `outcome` stays null because
nothing was ever compared, and a cancellation is not a result.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from twfi.console import use_utf8_output
from twfi.paths import repo_paths
from twfi.protocol import CHALLENGER_CANCELLED_REASON, CHALLENGER_STATUS

app = typer.Typer(add_completion=False, help=__doc__)

#: Lines of `ollama show` worth keeping: everything that identifies the weights and how they
#: were quantised. Capabilities matter too -- D-003 rests on qwen3.6:27b having vision.
_FIELDS = ("architecture", "parameters", "context length", "quantization")


def _ollama_digest(model: str) -> str | None:
    """The installed model's id, from `ollama list`. ``None`` if it is not installed."""
    result = subprocess.run(["ollama", "list"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[0] == model:
            return parts[1]
    return None


def _ollama_metadata(model: str) -> dict[str, Any]:
    """Architecture, size, quantisation and capabilities. Metadata only -- no weights load."""
    result = subprocess.run(["ollama", "show", model], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return {}
    out: dict[str, Any] = {}
    capabilities: list[str] = []
    section = ""
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        if not raw.startswith("    ") and raw.startswith("  "):
            section = line.casefold()
            continue
        if section == "capabilities":
            capabilities.append(line)
            continue
        for field in _FIELDS:
            if line.startswith(field):
                out[field.replace(" ", "_")] = line[len(field) :].strip()
    if capabilities:
        out["capabilities"] = capabilities
    return out


def _ollama_version() -> str | None:
    result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    match = re.search(r"\d+\.\d+\.\d+", result.stdout)
    return match.group() if match else result.stdout.strip() or None


def _hf_revision(model: str, cache: Path) -> str | None:
    """The cached revision of a Hugging Face repo, read from disk rather than fetched.

    Offline on purpose: the protocol forbids the test and build path from needing network, and
    a revision resolved online could differ from the weights actually on this machine -- which
    is precisely the divergence a lock exists to catch.
    """
    folder = cache / f"models--{model.replace('/', '--')}"
    refs = folder / "refs" / "main"
    if refs.is_file():
        return refs.read_text(encoding="utf-8").strip() or None
    snapshots = folder / "snapshots"
    if snapshots.is_dir():
        names = sorted(child.name for child in snapshots.iterdir() if child.is_dir())
        return names[-1] if names else None
    return None


@app.command()
def main(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Report without writing models.lock.json.")
    ] = False,
    hf_cache: Annotated[
        Path | None, typer.Option(help="Override the Hugging Face cache directory.")
    ] = None,
) -> None:
    """Observe the installed models, compare against the declaration, and lock or fail."""
    paths = repo_paths()
    declared_path = paths.root / "configs" / "models.yaml"
    if not declared_path.is_file():
        typer.echo(f"{declared_path.relative_to(paths.root)} is missing")
        raise typer.Exit(code=2)
    declared = yaml.safe_load(declared_path.read_text(encoding="utf-8"))
    if not isinstance(declared, dict):
        typer.echo("models.yaml must hold a mapping")
        raise typer.Exit(code=2)

    cache = hf_cache or (Path.home() / ".cache" / "huggingface" / "hub")
    roles = declared.get("roles")
    if not isinstance(roles, dict):
        typer.echo("models.yaml has no roles mapping")
        raise typer.Exit(code=2)

    locked: dict[str, Any] = {}
    problems: list[str] = []
    for role, spec in sorted(roles.items()):
        if not isinstance(spec, dict):
            problems.append(f"roles.{role} is not a mapping")
            continue
        model = str(spec.get("model", ""))
        backend = str(spec.get("backend", ""))
        entry: dict[str, Any] = {"model": model, "backend": backend}
        if backend == "ollama":
            observed = _ollama_digest(model)
            entry["digest_observed"] = observed
            entry["digest_declared"] = str(spec.get("digest", "")) or None
            entry.update(_ollama_metadata(model))
            if observed is None:
                problems.append(f"roles.{role}: {model} is not installed in ollama")
            elif entry["digest_declared"] and not observed.startswith(
                str(entry["digest_declared"])
            ):
                problems.append(
                    f"roles.{role}: {model} declares digest {entry['digest_declared']} but the "
                    f"installed one is {observed}. Not rewriting the lock to match: protocol 2.2 "
                    "fixes the model in advance, and a lock that follows whatever is installed "
                    "pins nothing."
                )
        else:
            revision = _hf_revision(model, cache)
            entry["revision_observed"] = revision
            entry["revision_declared"] = str(spec.get("revision", "")) or None
            if revision is None:
                problems.append(
                    f"roles.{role}: {model} is not in the local Hugging Face cache at {cache}"
                )
            elif entry["revision_declared"] and entry["revision_declared"] != revision:
                problems.append(
                    f"roles.{role}: {model} declares revision {entry['revision_declared']} but "
                    f"the cache holds {revision}"
                )
        locked[role] = entry
        mark = "ok  " if not problems or role not in str(problems[-1:]) else "!!  "
        identity = entry.get("digest_observed") or entry.get("revision_observed") or "-"
        typer.echo(f"  {mark}{role:<12} {model:<34} {identity}")

    payload: dict[str, Any] = {
        "roles": locked,
        "ollama_version": _ollama_version(),
        "chart_challenger": {
            "model": str((declared.get("chart_challenger") or {}).get("model", "")),
            "status": CHALLENGER_STATUS,
            "status_reason": CHALLENGER_CANCELLED_REASON,
            # Never filled by this script. A cancellation is not a result, and `outcome` is
            # the field a reader would take as one.
            "outcome": None,
        },
        "excluded": declared.get("excluded", []),
    }

    typer.echo("")
    if problems:
        typer.echo(f"FAILED: {len(problems)} problem(s); models.lock.json not written")
        for problem in problems:
            typer.echo(f"  - {problem}")
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo("--dry-run: models.lock.json not written")
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    destination = paths.root / "configs" / "models.lock.json"
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    typer.echo(f"wrote: {destination.relative_to(paths.root)}")
    typer.echo("")
    typer.echo("The chart challenger is locked as cancelled with outcome null (D-021).")


def _entrypoint() -> None:
    use_utf8_output()
    app()


if __name__ == "__main__":
    _entrypoint()
