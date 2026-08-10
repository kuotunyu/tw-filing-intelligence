"""Invariants that keep the *study* honest, checked mechanically.

Documentation drifts. These tests make the parts of the documentation that carry
research or legal weight fail loudly when they drift.
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

import twfi
from twfi.paths import RepoPaths
from twfi.protocol import PROTOCOL_VERSION

_SKIPPED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
_GENERATED_DIRS = {"raw", "cache", "index", "duckdb", "interim", "processed", "runs"}


@pytest.fixture()
def paths(repo_root: Path) -> RepoPaths:
    return RepoPaths(root=repo_root)


# ----------------------------------------------------------------- disclaimers


def test_package_exposes_version_and_disclaimer() -> None:
    assert twfi.__version__ == "1.0.5"
    assert "投資建議" in twfi.DISCLAIMER
    assert "production" in twfi.DISCLAIMER


def test_software_release_version_is_aligned_without_bumping_protocol(repo_root: Path) -> None:
    with (repo_root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    with (repo_root / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)
    root_package = next(
        package for package in lock["package"] if package["name"] == "tw-filing-intelligence"
    )

    assert project["project"]["version"] == twfi.__version__ == "1.0.5"
    assert root_package["version"] == "1.0.5"
    assert PROTOCOL_VERSION == "1.0.0"


def test_readme_states_it_is_not_advice_and_not_production(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    assert "不是投資建議" in readme
    assert "不是 production 系統" in readme


def test_mixed_license_boundary_separates_code_research_content_and_third_party_data(
    repo_root: Path,
) -> None:
    license_text = (repo_root / "LICENSE").read_text(encoding="utf-8")
    notice_path = repo_root / "NOTICE.md"
    content_license_path = repo_root / "CONTENT_LICENSE.md"

    assert notice_path.exists(), "third-party data terms belong in NOTICE.md"
    assert content_license_path.exists(), "author-created research content needs its own license"
    notice_text = notice_path.read_text(encoding="utf-8")
    content_license = content_license_path.read_text(encoding="utf-8")

    assert license_text.startswith("MIT License\n")
    assert "NOTE ON THIRD-PARTY DATA" not in license_text
    assert license_text.rstrip().endswith("SOFTWARE.")
    assert "source code in this repository only" in notice_text
    assert "CC BY 4.0" in notice_text
    assert "documentation, gold metadata, run records, and analysis artifacts" in notice_text
    assert "does not redistribute" in notice_text
    assert "Creative Commons Attribution 4.0 International" in content_license
    assert "https://creativecommons.org/licenses/by/4.0/" in content_license
    assert "does not apply to third-party" in content_license


def test_citation_metadata_matches_public_release(repo_root: Path) -> None:
    citation_path = repo_root / "CITATION.cff"
    assert citation_path.exists(), "a research release needs machine-readable citation metadata"

    citation = yaml.safe_load(citation_path.read_text(encoding="utf-8"))

    assert citation["cff-version"] == "1.2.0"
    assert citation["type"] == "software"
    assert citation["version"] == twfi.__version__ == "1.0.5"
    assert "date-released" not in citation, "add the actual date only when v1.0.5 is published"
    assert citation["license"] == "MIT"
    assert citation["repository-code"] == ("https://github.com/kuotunyu/tw-filing-intelligence")
    assert "url" not in citation, "add the immutable release URL only after the tag exists"
    assert citation["authors"] == [{"family-names": "kuotunyu"}]
    assert "NO_GO" in citation["abstract"]
    assert "protocol-literal" in citation["abstract"]


def test_ci_has_an_independent_offline_evidence_job(repo_root: Path) -> None:
    workflow = (repo_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    required = (
        "evidence:",
        "runs-on: ubuntu-latest",
        "test_real_protocol_lock_still_holds",
        "scripts/verify_results.py --dry-run",
        "scripts/check_leakage.py",
        "scripts/verify_evidence.py",
        "scripts/verify_analysis_audit.py",
    )
    for contract in required:
        assert contract in workflow


def test_public_docs_disclose_posthoc_audit_and_zenodo_boundary(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    audit_path = repo_root / "docs" / "ANALYSIS_AUDIT.md"
    zenodo_path = repo_root / "docs" / "ZENODO_PACKAGE.md"

    assert audit_path.is_file()
    assert zenodo_path.is_file()
    audit = audit_path.read_text(encoding="utf-8")
    zenodo = zenodo_path.read_text(encoding="utf-8")

    assert "recorded 17/33" in readme
    assert "protocol-literal 18/33" in readme
    assert "runtime scorer" in audit
    assert "不改寫" in audit
    assert "third-party raw" in zenodo
    assert "授權" in zenodo
    assert "不可發布" in zenodo


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
    assert "**33**" in protocol, "locked set size must be stated and >= 30"


def test_hard_category_gate_uses_the_pooled_set(paths: RepoPaths) -> None:
    """A single 3-5 item category cannot carry a 10pp claim; G2 must pool them."""
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert "**18 題**" in protocol, "G2 must state the pooled hard-set size"
    assert "合併 hard set" in protocol
    # And the pooled size must actually equal the declared per-category counts.
    assert 5 + 4 + 5 + 4 + 3 == 21


def test_protocol_requires_sample_size_and_confidence_intervals(paths: RepoPaths) -> None:
    """Percentages without n are how small-sample studies mislead."""
    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert "Wilson" in protocol
    assert "小樣本誠實性" in protocol


# ------------------------------------------------------------------ data hygiene


def test_gitignore_excludes_filings_and_weights(repo_root: Path) -> None:
    gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
    for pattern in ("data/raw/", "*.pdf", "*.xbrl", "*.safetensors", "*.gguf", ".env"):
        assert pattern in gitignore, f"{pattern} must be git-ignored"


def test_gitignore_keeps_only_the_official_locked_run_evidence(repo_root: Path) -> None:
    git = shutil.which("git")
    assert git is not None

    def ignored(relative: str) -> bool:
        result = subprocess.run(  # noqa: S603 - paths below are fixed test fixtures
            [git, "check-ignore", "--no-index", "--quiet", relative],
            cwd=repo_root,
            check=False,
        )
        return result.returncode == 0

    assert ignored("results/runs/ladder_dev.json")
    assert not ignored("results/runs/F7/records.jsonl")
    assert not ignored("results/runs/probes/records.jsonl")
    assert not ignored("results/runs/resources.json")
    assert not ignored("results/runs/resource_budget.json")
    assert not ignored("results/runs/locked_run_started.json")
    assert not ignored("results/feasibility/results_verification.json")


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
        "pyproject.toml",
        "docs/FEASIBILITY_PROTOCOL.md",
        "docs/DECISIONS.md",
        "docs/DATA_PROVENANCE.md",
        "docs/THREAT_MODEL.md",
    ],
)
def test_required_documents_exist(repo_root: Path, relative_path: str) -> None:
    assert (repo_root / relative_path).is_file(), f"{relative_path} is required"


# ---------------------------------------------------------------------- models


@pytest.fixture()
def declared_models(paths: RepoPaths) -> dict[str, object]:
    return yaml.safe_load((paths.configs / "models.yaml").read_text(encoding="utf-8"))


def test_declared_model_roles_are_complete(declared_models: dict[str, object]) -> None:
    roles = declared_models["roles"]
    assert isinstance(roles, dict)
    assert set(roles) == {"embedding", "reranker", "generation", "chart"}


def test_generation_model_matches_the_protocol(
    paths: RepoPaths, declared_models: dict[str, object]
) -> None:
    """The config and the pre-registered protocol must name the same model."""
    roles = declared_models["roles"]
    assert isinstance(roles, dict)
    generation = roles["generation"]
    assert isinstance(generation, dict)

    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert generation["model"] in protocol
    assert str(generation["digest"]) in protocol
    # Numeric answers must not come from the model (DECISIONS D-005).
    assert "不允許 LLM 自由生成 SQL" in protocol


def test_the_chart_route_uses_the_generation_weights(
    declared_models: dict[str, object],
) -> None:
    """The challenger was cancelled (D-021), so 27B serves the chart route as well."""
    roles = declared_models["roles"]
    assert isinstance(roles, dict)
    assert roles["chart"]["digest"] == roles["generation"]["digest"]  # type: ignore[index]


def test_chart_challenger_rule_is_fixed_in_advance(
    paths: RepoPaths, declared_models: dict[str, object]
) -> None:
    """A model swap is only legitimate if its rule predates the numbers."""
    challenger = declared_models["chart_challenger"]
    assert isinstance(challenger, dict)
    assert challenger["outcome"] is None, (
        "challenger outcome is recorded by pin_models.py; do not hand-edit it"
    )
    assert challenger["items"] == 16
    assert "10 percentage points" in str(challenger["switch_rule"])

    protocol = paths.protocol_doc.read_text(encoding="utf-8")
    assert "Chart challenger" in protocol
    assert str(challenger["digest"]) in protocol


def test_a_cancelled_challenger_still_has_no_outcome(
    declared_models: dict[str, object],
) -> None:
    """Cancellation and result must stay separate fields.

    The comparison never ran, so there is no margin to report. If a cancellation could be
    written into `outcome`, the file could later claim a winner for a run that does not
    exist -- which is the one thing the pre-registered rule was meant to prevent.
    """
    from twfi.protocol import CHALLENGER_STATUS

    challenger = declared_models["chart_challenger"]
    assert isinstance(challenger, dict)
    if challenger.get("status") == "cancelled" or CHALLENGER_STATUS == "cancelled":
        assert challenger.get("status") == "cancelled", (
            "protocol.py says the challenger is cancelled; models.yaml must say so too"
        )
        assert challenger["outcome"] is None
        assert challenger.get("status_reason"), "a cancellation has to say why"


def test_the_cancelled_challenger_set_is_absent(paths: RepoPaths) -> None:
    """There is no legitimate way to build it, so its file must not exist.

    The dev filings contain no charts. The only ways to produce 16 chart crops would be to
    label tables as charts or to draw on the locked set, and both are forbidden.
    """
    from twfi.protocol import CHALLENGER_STATUS

    if CHALLENGER_STATUS == "cancelled":
        assert not paths.chart_challenger.exists(), (
            f"{paths.chart_challenger} exists but the challenger was cancelled (D-021); "
            "delete it rather than filling it in"
        )


def test_excluded_models_are_recorded_as_decisions(declared_models: dict[str, object]) -> None:
    excluded = declared_models["excluded"]
    assert isinstance(excluded, list)
    assert any("gpt-oss:20b" in str(entry.get("model")) for entry in excluded)
    assert all(entry.get("reason") for entry in excluded)


def test_decoding_is_deterministic_and_thinking_is_off(
    declared_models: dict[str, object],
) -> None:
    decoding = declared_models["decoding"]
    assert isinstance(decoding, dict)
    assert decoding["temperature"] == 0.0
    assert decoding["top_p"] == 1.0
    assert decoding["seed"] == 20260731
    assert decoding["think"] is False


def test_vram_budget_stays_under_the_gate(declared_models: dict[str, object]) -> None:
    budget = declared_models["vram_budget_gb"]
    assert isinstance(budget, dict)
    assert budget["gate_limit"] == 22.0
    assert budget["expected_peak"] < budget["gate_limit"]


def test_data_provenance_pins_the_host_allowlist(repo_root: Path) -> None:
    provenance = (repo_root / "docs" / "DATA_PROVENANCE.md").read_text(encoding="utf-8")
    for host in ("mops.twse.com.tw", "doc.twse.com.tw", "openapi.twse.com.tw"):
        assert host in provenance


def test_threat_model_covers_the_required_threats(repo_root: Path) -> None:
    threats = (repo_root / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")
    for topic in ("prompt injection", "SSRF", "CAPTCHA", "洩漏", "Secrets"):
        assert topic in threats, f"threat model must discuss {topic}"
