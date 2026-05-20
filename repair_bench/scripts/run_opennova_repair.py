"""OpenNova Model-Driven Repair: runs the code_repair skill on each bug sample.

Uses AgentRuntime to invoke the code_repair skill, which executes the 6-stage
intelligent model-driven repair workflow (static analysis -> test feedback ->
classification -> planning -> patch -> validation).

Usage:
    python repair_bench/scripts/run_opennova_repair.py [--model deepseek-v4-pro] [--mock]
    python repair_bench/scripts/run_opennova_repair.py --dataset division_by_zero_001
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

_REPAIR_BENCH = _PROJECT_ROOT / "repair_bench"
_DATASETS = _REPAIR_BENCH / "datasets"
_REPORTS = _REPAIR_BENCH / "reports"
_REPORTS.mkdir(parents=True, exist_ok=True)

MOCK_RESULT_TEMPLATE = {
    "bug_detected": True,
    "bug_type": "unknown",
    "true_bug_type": "",
    "location": {"file": "buggy.py", "line": 1, "function": ""},
    "static_analysis": {
        "suspicious_patterns": ["mock pattern"],
        "candidate_lines": [1],
    },
    "test_feedback": {
        "tests_run": 3,
        "tests_failed_before": 1,
        "failing_tests": ["test_mock"],
        "error_messages": ["mock error"],
    },
    "classification": {
        "predicted_type": "unknown",
        "confidence": "medium",
        "reasoning": "mock classification",
    },
    "repair": {
        "root_cause": "mock cause",
        "fix_description": "mock fix",
        "lines_changed": [1],
        "patch_summary": "mock patch",
    },
    "validation": {
        "tests_passed_after": 3,
        "tests_failed_after": 0,
        "all_passing": True,
        "repair_attempts": 1,
    },
    "remaining_risks": "none",
    "method": "opennova_repair",
    "runtime_seconds": 0.0,
}


def run_repair_cli(sample_dir: str, model: str | None = None, provider: str = "deepseek") -> dict:
    """Run OpenNova repair via CLI subprocess on a single sample."""
    import subprocess

    target = _DATASETS / sample_dir
    if not (target / "buggy.py").exists():
        return {"sample": sample_dir, "error": "buggy.py not found", "bug_detected": False}

    task = f"/skill code_repair {target}"

    # Save original buggy.py to restore after repair
    buggy_path = target / "buggy.py"
    original_code = buggy_path.read_text()

    # Ensure auto_confirm is enabled via project-local config
    config_path = _PROJECT_ROOT / ".opennova" / "config.yaml"
    config_backup = None
    if config_path.exists():
        config_backup = config_path.read_text()

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        import yaml

        config_data = {"agent": {"auto_confirm": True, "max_iterations": 200}}
        config_path.write_text(yaml.dump(config_data))

        cmd = ["uv", "run", "opennova", "run", "--no-tui", "--provider", provider]
        if model:
            cmd.extend(["-m", model])
        cmd.append(task)

        start = time.time()
        result = subprocess.run(
            cmd,
            cwd=str(_PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=600,  # 10 min per sample
        )
        elapsed = time.time() - start
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        return {
            "sample": sample_dir,
            "error": "timeout after 600s",
            "bug_detected": False,
            "method": "opennova_repair",
            "runtime_seconds": round(elapsed, 2),
        }
    finally:
        # Restore or remove temporary config
        if config_backup is not None:
            config_path.write_text(config_backup)
        else:
            config_path.unlink(missing_ok=True)
        # Restore original buggy code
        buggy_path.write_text(original_code)

    # Try to read the generated repair_result.json
    result_path = target / "repair_result.json"
    if result_path.exists():
        try:
            data = json.loads(result_path.read_text())
            data["sample"] = sample_dir
            data["method"] = "opennova_repair"
            data["runtime_seconds"] = round(elapsed, 2)
            data["stdout_tail"] = result.stdout[-2000:] if result.stdout else ""
            return data
        except json.JSONDecodeError:
            pass

    return {
        "sample": sample_dir,
        "error": "No repair_result.json produced",
        "bug_detected": False,
        "method": "opennova_repair",
        "runtime_seconds": round(elapsed, 2),
        "stdout": result.stdout[-3000:] if result.stdout else "",
        "stderr": result.stderr[-1000:] if result.stderr else "",
        "return_code": result.returncode,
    }


def run_repair_mock(sample_dir: str) -> dict:
    """Generate mock repair result for CI/testing."""
    import time as _time

    target = _DATASETS / sample_dir
    metadata_path = target / "metadata.json"

    result = json.loads(json.dumps(MOCK_RESULT_TEMPLATE))
    result["sample"] = sample_dir

    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        result["true_bug_type"] = metadata.get("bug_type", "")
        result["bug_type"] = metadata.get("bug_type", "unknown")
        result["classification"]["predicted_type"] = metadata.get("bug_type", "unknown")
        result["location"]["line"] = metadata.get("expected_fix_line", 1)
        result["repair"]["root_cause"] = metadata.get("description", "")
        result["repair"]["fix_description"] = metadata.get("known_fix_description", "")

    result["runtime_seconds"] = round(_time.time() % 1.0 + 0.5, 2)
    return result


def main():
    parser = argparse.ArgumentParser(description="OpenNova Model-Driven Repair")
    parser.add_argument("--model", default=None, help="LLM model override")
    parser.add_argument("--provider", default="deepseek", help="LLM provider (openai, anthropic, deepseek)")
    parser.add_argument("--mock", action="store_true", help="Use mock responses (no API calls)")
    parser.add_argument("--dataset", help="Run on a specific dataset sample only")
    parser.add_argument(
        "--output", default=str(_REPORTS / "opennova_repair_results.json"), help="Output file"
    )
    args = parser.parse_args()

    if args.dataset:
        samples = [args.dataset]
    else:
        samples = sorted(
            [d.name for d in _DATASETS.iterdir() if d.is_dir() and (d / "buggy.py").exists()]
        )

    print(f"Running OpenNova Repair on {len(samples)} samples...")
    print(f"Provider: {args.provider}, Model: {args.model or 'default'}")
    if args.mock:
        print("[MOCK MODE] No API calls will be made.")

    results = []
    for i, sample in enumerate(samples):
        print(f"  [{i + 1}/{len(samples)}] {sample}...", end=" ", flush=True)

        if args.mock:
            r = run_repair_mock(sample)
        else:
            r = run_repair_cli(sample, model=args.model, provider=args.provider)

        bug = "BUG" if r.get("bug_detected") else "OK" if "error" not in r else "ERR"
        bug_type = r.get("bug_type", "?")
        runtime = r.get("runtime_seconds", 0)
        print(f"{bug} ({bug_type}) in {runtime}s")
        results.append(r)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")

    detected = sum(1 for r in results if r.get("bug_detected"))
    all_passing = sum(1 for r in results if r.get("validation", {}).get("all_passing"))
    print(f"Detection rate: {detected}/{len(results)}")
    print(f"Repair success rate: {all_passing}/{len(results)}")


if __name__ == "__main__":
    main()
