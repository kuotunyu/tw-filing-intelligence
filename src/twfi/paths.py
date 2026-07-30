"""Repository-relative path resolution.

All filesystem locations used by the harness go through :class:`RepoPaths` so that
tests can point the whole harness at a temporary directory and never touch the
real ``results/feasibility/`` artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from twfi.errors import RepoLayoutError

__all__ = ["RepoPaths", "find_repo_root", "repo_paths"]

_ROOT_MARKER = "pyproject.toml"

#: Directories that hold generated data and must never be committed.
_GENERATED_SUBDIRS = (
    ("data", "raw"),
    ("data", "raw", "manual"),
    ("data", "interim"),
    ("data", "processed"),
    ("data", "cache"),
    ("data", "index"),
    ("data", "duckdb"),
    ("results", "runs"),
)


def find_repo_root(start: Path | None = None) -> Path:
    """Return the nearest ancestor directory containing ``pyproject.toml``.

    Args:
        start: Where to begin the upward search. Defaults to this module's location.

    Raises:
        RepoLayoutError: If no ancestor contains the root marker.
    """
    origin = (start if start is not None else Path(__file__)).resolve()
    candidates = (origin, *origin.parents) if origin.is_dir() else origin.parents
    for candidate in candidates:
        if (candidate / _ROOT_MARKER).is_file():
            return candidate
    raise RepoLayoutError(f"no {_ROOT_MARKER} found at or above {origin}")


@dataclass(frozen=True, slots=True)
class RepoPaths:
    """Resolved locations of every directory the harness reads or writes."""

    root: Path

    # -- committed inputs -------------------------------------------------
    @property
    def docs(self) -> Path:
        return self.root / "docs"

    @property
    def configs(self) -> Path:
        return self.root / "configs"

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    @property
    def manifests(self) -> Path:
        return self.root / "data" / "manifests"

    @property
    def evaluation(self) -> Path:
        return self.root / "data" / "evaluation"

    @property
    def dev_gold(self) -> Path:
        return self.evaluation / "dev" / "gold.jsonl"

    @property
    def locked_gold(self) -> Path:
        return self.evaluation / "locked" / "gold.jsonl"

    @property
    def locked_probes(self) -> Path:
        return self.evaluation / "locked" / "probes.jsonl"

    # -- generated data (never committed) ---------------------------------
    @property
    def raw(self) -> Path:
        return self.root / "data" / "raw"

    @property
    def manual_raw(self) -> Path:
        return self.raw / "manual"

    @property
    def cache(self) -> Path:
        return self.root / "data" / "cache"

    @property
    def index(self) -> Path:
        return self.root / "data" / "index"

    @property
    def duckdb(self) -> Path:
        return self.root / "data" / "duckdb"

    @property
    def runs(self) -> Path:
        return self.root / "results" / "runs"

    # -- committed outputs ------------------------------------------------
    @property
    def feasibility(self) -> Path:
        return self.root / "results" / "feasibility"

    @property
    def summary_json(self) -> Path:
        return self.feasibility / "summary.json"

    @property
    def error_analysis_jsonl(self) -> Path:
        return self.feasibility / "error_analysis.jsonl"

    @property
    def go_no_go_json(self) -> Path:
        return self.feasibility / "GO_NO_GO.json"

    @property
    def protocol_lock_json(self) -> Path:
        return self.feasibility / "protocol_lock.json"

    @property
    def protocol_doc(self) -> Path:
        return self.docs / "FEASIBILITY_PROTOCOL.md"

    @property
    def models_lock_json(self) -> Path:
        return self.configs / "models.lock.json"

    def ensure_generated_dirs(self) -> None:
        """Create the generated-data directories if they are missing.

        Only touches directories that are git-ignored; committed directories are
        never created implicitly.
        """
        for parts in _GENERATED_SUBDIRS:
            (self.root.joinpath(*parts)).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def repo_paths() -> RepoPaths:
    """Return the cached :class:`RepoPaths` for the checked-out repository."""
    return RepoPaths(root=find_repo_root())
