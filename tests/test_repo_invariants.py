"""Invariants that keep the *study* honest, checked mechanically.

Documentation drifts. These tests make the parts of the documentation that carry
research or legal weight fail loudly when they drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import twfi
from twfi.paths import RepoPaths

_SKIPPED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
_GENERATED_DIRS = {"raw", "cache", "index", "duckdb", "interim", "processed", "runs"}


@pytest.fixture()
def paths(repo_root: Path) -> RepoPaths:
    return RepoPaths(root=repo_root)


# ----------------------------------------------------------------- disclaimers


def test_package_exposes_version_and_disclaimer() -> None:
    assert twfi.__version__ == "0.1.0"
    assert "投資建議" in twfi.DISCLAIMER
    assert "production" in twfi.DISCLAIMER


def test_readme_states_it_is_not_advice_and_not_production(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "不是投資建議" in readme
    assert "不是 production 系統" in readme


def test_license_excludes_third_party_filings(repo_root: Path) -> None:
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    assert "source code in this repository only" in license_text
    assert "does not redistribute" in license_text


# -------------------------------------------------------------------- protocol


@pytest.mark.parametrize("gate", [f"G{i}" for i in range(1, 11)])
def test_protocol_declares_every_gate(paths: RepoPaths, gate: str) -> None:
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert f"| {gate} |" in protocol, f"{gate} is missing from the gate table"


@pytest.mark.parametrize(
    "question_type",
    [
        "narrative_fact",
        "table_cell",
        "numeric_calculation",
        "cross_period_comparison",
        "chart_value_trend",
        "cross_page",
        "cross_document",
        "unanswerable",
    ],
)
def test_protocol_declares_every_question_type(paths: RepoPaths, question_type: str) -> None:
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert question_type in protocol


@pytest.mark.parametrize("decision", ["GO", "CONDITIONAL_GO", "NO_GO"])
def test_protocol_declares_every_decision_outcome(paths: RepoPaths, decision: str) -> None:
    assert decision in paths.protocol_doc.read_text(encoding="utf-8")


@pytest.mark.parametrize("factor", [f"F{i}" for i in range(8)])
def test_protocol_declares_the_full_factor_ladder(paths: RepoPaths, factor: str) -> None:
    assert factor in paths.protocol_doc.read_text(encoding="utf-8")


def test_protocol_keeps_dev_and_locked_companies_disjoint(paths: RepoPaths) -> None:
    """The split declared in the protocol must actually be company-disjoint."""
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    dev_codes = {"2412", "1301"}
    locked_codes = {"2330", "2317", "2882"}
    assert not dev_codes & locked_codes
    for code in dev_codes | locked_codes:
        assert code in protocol, f"company {code} is not declared in the protocol"


def test_locked_question_count_is_at_least_thirty(paths: RepoPaths) -> None:
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert "**36**" in protocol, "locked set size must be stated and >= 30"


# ------------------------------------------------------------------ data hygiene


def test_gitignore_excludes_filings_and_weights(repo_root: Path) -> None:
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("data/raw/", "*.pdf", "*.xbrl", "*.safetensors", "*.gguf", ".env"):
        assert pattern in gitignore, f"{pattern} must be git-ignored"


def test_no_filings_are_stored_outside_generated_directories(repo_root: Path) -> None:
    """Original PDFs / XBRL are never redistributed from this repository."""
    offenders: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".xbrl", ".zip"}:
            continue
        parts = set(path.relative_to(repo_root).parts)
        if parts & _SKIPPED_DIRS or parts & _GENERATED_DIRS:
            continue
        offenders.append(str(path.relative_to(repo_root)))
    assert offenders == [], f"filings must not live in committed paths: {offenders}"


def test_source_never_reads_dotenv(repo_root: Path) -> None:
    """Tests must not depend on ``.env``; the simplest guarantee is never reading one."""
    offenders: list[str] = []
    for path in (repo_root / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "dotenv" in text or "env_file" in text:
            offenders.append(str(path.relative_to(repo_root)))
    assert offenders == [], f"dotenv usage found in {offenders}"


# ------------------------------------------------------------------------ docs


@pytest.mark.parametrize(
    "relative_path",
    [
        "README.md",
        "LICENSE",
        "CLAUDE.md",
        "pyproject.toml",
        "docs/FEASIBILITY_PROTOCOL.md",
        "docs/IMPLEMENTATION_PLAN.md",
        "docs/DECISIONS.md",
        "docs/DATA_PROVENANCE.md",
        "docs/THREAT_MODEL.md",
        "docs/PROGRESS.md",
    ],
)
def test_required_documents_exist(repo_root: Path, relative_path: str) -> None:
    assert (repo_root / relative_path).is_file(), f"{relative_path} is required"


def test_project_skills_have_frontmatter(repo_root: Path) -> None:
    skill_files = sorted((repo_root / ".claude" / "skills").glob("*/SKILL.md"))
    assert skill_files, "expected project-level skills under .claude/skills/"
    for skill in skill_files:
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n"), f"{skill.name} needs YAML frontmatter"
        header = text.split("---", 2)[1]
        assert "name:" in header and "description:" in header, skill.parent.name


def test_data_provenance_pins_the_host_allowlist(repo_root: Path) -> None:
    provenance = (repo_root / "docs" / "DATA_PROVENANCE.md").read_text(encoding="utf-8")
    for host in ("mops.twse.com.tw", "doc.twse.com.tw", "openapi.twse.com.tw"):
        assert host in provenance


def test_threat_model_covers_the_required_threats(repo_root: Path) -> None:
    threats = (repo_root / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
    for topic in ("prompt injection", "SSRF", "CAPTCHA", "洩漏", "Secrets"):
        assert topic in threats, f"threat model must discuss {topic}"
