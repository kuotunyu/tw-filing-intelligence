"""Path resolution must be repo-relative and must never guess."""

from __future__ import annotations

from pathlib import Path

import pytest

from twfi.errors import RepoLayoutError
from twfi.paths import RepoPaths, find_repo_root, repo_paths


def test_find_repo_root_from_directory(sandbox_repo: Path) -> None:
    nested = sandbox_repo / "a" / "b"
    nested.mkdir(parents=True)
    assert find_repo_root(nested) == sandbox_repo


def test_find_repo_root_from_file(sandbox_repo: Path) -> None:
    target = sandbox_repo / "src" / "mod.py"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    assert find_repo_root(target) == sandbox_repo


def test_find_repo_root_raises_without_marker(tmp_path: Path) -> None:
    lonely = tmp_path / "no" / "marker" / "here"
    lonely.mkdir(parents=True)
    with pytest.raises(RepoLayoutError):
        find_repo_root(lonely)


def test_real_repo_root_has_expected_layout(repo_root: Path) -> None:
    paths = RepoPaths(root=repo_root)
    assert paths.protocol_doc.is_file()
    assert paths.docs.is_dir()
    assert (paths.root / "src" / "twfi" / "__init__.py").is_file()


@pytest.mark.parametrize(
    ("attr", "expected"),
    [
        ("docs", "docs"),
        ("configs", "configs"),
        ("scripts", "scripts"),
        ("manifests", "data/manifests"),
        ("evaluation", "data/evaluation"),
        ("dev_gold", "data/evaluation/dev/gold.jsonl"),
        ("locked_gold", "data/evaluation/locked/gold.jsonl"),
        ("locked_probes", "data/evaluation/locked/probes.jsonl"),
        ("raw", "data/raw"),
        ("manual_raw", "data/raw/manual"),
        ("cache", "data/cache"),
        ("index", "data/index"),
        ("duckdb", "data/duckdb"),
        ("runs", "results/runs"),
        ("feasibility", "results/feasibility"),
        ("summary_json", "results/feasibility/summary.json"),
        ("error_analysis_jsonl", "results/feasibility/error_analysis.jsonl"),
        ("go_no_go_json", "results/feasibility/GO_NO_GO.json"),
        ("protocol_lock_json", "results/feasibility/protocol_lock.json"),
        ("protocol_doc", "docs/FEASIBILITY_PROTOCOL.md"),
        ("models_lock_json", "configs/models.lock.json"),
    ],
)
def test_every_path_is_repo_relative(sandbox_repo: Path, attr: str, expected: str) -> None:
    paths = RepoPaths(root=sandbox_repo)
    resolved = getattr(paths, attr)
    assert resolved == sandbox_repo.joinpath(*expected.split("/"))


def test_ensure_generated_dirs_only_creates_ignored_dirs(sandbox_repo: Path) -> None:
    paths = RepoPaths(root=sandbox_repo)
    paths.ensure_generated_dirs()

    for created in (
        paths.raw,
        paths.manual_raw,
        paths.cache,
        paths.index,
        paths.duckdb,
        paths.runs,
    ):
        assert created.is_dir()

    # Committed directories are never created implicitly.
    assert not paths.manifests.exists()
    assert not paths.feasibility.exists()


def test_ensure_generated_dirs_is_idempotent(sandbox_repo: Path) -> None:
    paths = RepoPaths(root=sandbox_repo)
    paths.ensure_generated_dirs()
    paths.ensure_generated_dirs()
    assert paths.raw.is_dir()


def test_repo_paths_is_cached_and_frozen() -> None:
    first = repo_paths()
    assert repo_paths() is first
    with pytest.raises(AttributeError):
        first.root = Path("/somewhere")  # type: ignore[misc]
