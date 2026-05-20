"""Tests for the repair benchmark: dataset integrity, script mock modes, evaluation."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPAIR_BENCH = Path(__file__).resolve().parent.parent / "repair_bench"
DATASETS = REPAIR_BENCH / "datasets"
SCRIPTS = REPAIR_BENCH / "scripts"
REPORTS = REPAIR_BENCH / "reports"


# --- Dataset Integrity Tests ---


def test_all_samples_have_required_files():
    """Every bug sample must have buggy.py, test_buggy.py, and metadata.json."""
    for d in sorted(DATASETS.iterdir()):
        if not d.is_dir():
            continue
        assert (d / "buggy.py").exists(), f"{d.name}: missing buggy.py"
        assert (d / "test_buggy.py").exists(), f"{d.name}: missing test_buggy.py"
        assert (d / "metadata.json").exists(), f"{d.name}: missing metadata.json"


def test_metadata_has_required_fields():
    """Metadata must contain bug_type and description."""
    for d in sorted(DATASETS.iterdir()):
        if not d.is_dir():
            continue
        meta = json.loads((d / "metadata.json").read_text())
        assert "bug_type" in meta, f"{d.name}: metadata missing bug_type"
        assert "description" in meta, f"{d.name}: metadata missing description"


def test_all_bug_types_covered():
    """Verify all 9 bug types have at least one sample."""
    expected_types = {
        "division_by_zero",
        "index_out_of_range",
        "none_dereference",
        "type_mismatch",
        "missing_return",
        "mutable_default_argument",
        "off_by_one",
        "logic_error",
        "resource_leak",
        "arithmetic_operator_bug",
        "comparison_operator_bug",
        "variable_misuse",
    }
    found_types = set()
    for d in sorted(DATASETS.iterdir()):
        if d.is_dir() and (d / "metadata.json").exists():
            meta = json.loads((d / "metadata.json").read_text())
            found_types.add(meta["bug_type"])
    missing = expected_types - found_types
    assert not missing, f"Missing bug types: {missing}"


def test_buggy_code_has_at_least_one_failing_test():
    """For a representative sample, verify tests fail on buggy code."""
    sample = DATASETS / "division_by_zero_001"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(sample / "test_buggy.py"), "-q", "--tb=no"],
        capture_output=True,
        text=True,
        cwd=str(sample),
        timeout=30,
    )
    # Should have at least one failure
    assert "FAILED" in result.stdout or result.returncode != 0, (
        f"Expected at least one test failure in {sample.name}"
    )


def test_sample_count():
    """Should have at least 27 samples (original 27 + extended dataset)."""
    samples = [d for d in DATASETS.iterdir() if d.is_dir() and (d / "buggy.py").exists()]
    assert len(samples) >= 27, f"Expected at least 27 samples, got {len(samples)}"


# --- Script Tests ---


def test_pure_llm_mock_runs():
    """Pure LLM baseline should run in mock mode and produce valid output."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_pure_llm.py"), "--mock"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPAIR_BENCH.parent),
    )
    assert result.returncode == 0, f"run_pure_llm.py failed: {result.stderr}"
    assert "Results saved" in result.stdout

    output_file = REPORTS / "pure_llm_results.json"
    assert output_file.exists(), "pure_llm_results.json not created"

    data = json.loads(output_file.read_text())
    assert len(data) >= 27, f"Expected at least 27 results, got {len(data)}"
    assert all("sample" in r for r in data)
    assert all("bug_type" in r for r in data)


def test_repair_mock_runs():
    """OpenNova repair should run in mock mode and produce valid output."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "run_opennova_repair.py"), "--mock"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPAIR_BENCH.parent),
    )
    assert result.returncode == 0, f"run_opennova_repair.py failed: {result.stderr}"
    assert "Results saved" in result.stdout

    output_file = REPORTS / "opennova_repair_results.json"
    assert output_file.exists(), "opennova_repair_results.json not created"

    data = json.loads(output_file.read_text())
    assert len(data) >= 27, f"Expected at least 27 results, got {len(data)}"
    assert all("sample" in r for r in data)
    # Repair results should have validation field
    for r in data:
        assert "validation" in r or "error" in r


def test_evaluate_runs():
    """Evaluate script should produce comparison report."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "evaluate.py")],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=str(REPAIR_BENCH.parent),
    )
    assert result.returncode == 0, f"evaluate.py failed: {result.stderr}"

    assert (REPORTS / "comparison.md").exists(), "comparison.md not created"
    assert (REPORTS / "metrics.json").exists(), "metrics.json not created"


# --- Skill Tests ---


def test_code_repair_skill_exists():
    """The code_repair SKILL.md should exist in .opennova/skills/."""
    skill_path = Path(__file__).resolve().parent.parent / ".opennova" / "skills" / "code_repair" / "SKILL.md"
    assert skill_path.exists(), f"Skill not found at {skill_path}"

    content = skill_path.read_text()
    assert "---" in content, "Missing YAML frontmatter"
    assert "name: code_repair" in content, "Missing skill name"
    assert "Stage 1: Static Analysis" in content
    assert "Stage 6: Validation" in content
