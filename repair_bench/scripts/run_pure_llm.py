"""Pure LLM baseline: single-prompt bug detection without agent tools.

Usage:
    python repair_bench/scripts/run_pure_llm.py [--model gpt-4o] [--mock]
    python repair_bench/scripts/run_pure_llm.py --dataset division_by_zero_001
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

_REPAIR_BENCH = Path(__file__).resolve().parent.parent
_DATASETS = _REPAIR_BENCH / "datasets"
_REPORTS = _REPAIR_BENCH / "reports"
_REPORTS.mkdir(parents=True, exist_ok=True)

PROMPT = """Analyze the following Python code for defects and provide the COMPLETE corrected code.

{code}

Tasks:
1. Determine if the code contains a bug (true/false).
2. If yes, classify the bug type as one of:
   division_by_zero, index_out_of_range, none_dereference, type_mismatch,
   missing_return, mutable_default_argument, off_by_one, logic_error,
   resource_leak, unknown
3. Identify the bug location (file, line number, function name).
4. Explain the root cause in 1-2 sentences.
5. Propose a minimal fix.
6. Provide the COMPLETE fixed code for the ENTIRE file. The fixed_code must be the full
   corrected Python source code that can be written directly to buggy.py and pass tests.
   Only fix the identified bug — do not change anything else.

Return your answer as valid JSON only (no other text):
```json
{{
  "bug_detected": true,
  "bug_type": "<category>",
  "location": {{
    "file": "buggy.py",
    "line": <line_number>,
    "function": "<function_name>"
  }},
  "root_cause": "<explanation>",
  "fix_description": "<what to change>",
  "fixed_code": "<complete corrected Python source code>",
  "confidence": "<high|medium|low>"
}}
```
"""

_MOCK_FIXED_CODES = {
    "division_by_zero_001": "def divide(a, b):\n    if b == 0:\n        return None\n    return a / b\n",
    "division_by_zero_002": "def safe_divide(a, b):\n    if b <= 0:\n        return 0\n    return a / b\n",
    "division_by_zero_003": "def compute_ratio(part, total):\n    if total <= 0:\n        return 0.0\n    return part / total\n",
}

MOCK_RESULTS = {
    "division_by_zero_001": {
        "bug_detected": True,
        "bug_type": "division_by_zero",
        "location": {"file": "buggy.py", "line": 2, "function": "divide"},
        "root_cause": "No check for zero divisor in divide()",
        "fix_description": "Add zero check before division",
        "fixed_code": _MOCK_FIXED_CODES["division_by_zero_001"],
        "confidence": "high",
    },
    "division_by_zero_002": {
        "bug_detected": True,
        "bug_type": "division_by_zero",
        "location": {"file": "buggy.py", "line": 2, "function": "safe_divide"},
        "root_cause": "Condition b < 0 misses b == 0 case",
        "fix_description": "Change b < 0 to b <= 0",
        "fixed_code": _MOCK_FIXED_CODES["division_by_zero_002"],
        "confidence": "high",
    },
    "division_by_zero_003": {
        "bug_detected": True,
        "bug_type": "division_by_zero",
        "location": {"file": "buggy.py", "line": 3, "function": "compute_ratio"},
        "root_cause": "Falls through to division when total <= 0",
        "fix_description": "Guard total <= 0 case",
        "fixed_code": _MOCK_FIXED_CODES["division_by_zero_003"],
        "confidence": "high",
    },
}


def load_config():
    """Load OpenNova config to get API keys and model settings."""
    try:
        from opennova.config import load_config as _load

        return _load()
    except Exception:
        return {}


def get_provider(model: str, provider_name: str = "openai"):
    """Create an LLM provider instance."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

    from opennova.providers.factory import ProviderFactory

    factory = ProviderFactory()
    config = load_config()
    config.set("default_provider", provider_name)
    if model:
        config.set(f"providers.{provider_name}.default_model", model)
    return factory.create_provider(config)


def run_pure_llm(sample_dir: str, model: str = "gpt-4o", provider: str = "openai", mock: bool = False) -> dict:
    """Run pure LLM analysis on a single bug sample."""
    d = _DATASETS / sample_dir
    buggy_path = d / "buggy.py"
    metadata_path = d / "metadata.json"

    if not buggy_path.exists():
        return {"sample": sample_dir, "error": "buggy.py not found", "bug_detected": False}

    code = buggy_path.read_text()
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}

    start = time.time()

    if mock:
        result = MOCK_RESULTS.get(
            sample_dir,
            {
                "bug_detected": True,
                "bug_type": metadata.get("bug_type", "unknown"),
                "location": {"file": "buggy.py", "line": 1, "function": "unknown"},
                "root_cause": "Mock analysis",
                "fix_description": "Mock fix",
                "confidence": "medium",
            },
        )
    else:
        llm = get_provider(model, provider)
        prompt = PROMPT.format(code=code)

        import asyncio
        from opennova.providers.base import Message

        async def _call():
            response = await llm.chat(
                messages=[Message(role="user", content=prompt)],
                tools=None,
                temperature=0.3,
            )
            return response

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio

                nest_asyncio.apply()
            response = asyncio.run(_call())
        except RuntimeError:
            response = asyncio.run(_call())

        content = response.content if hasattr(response, "content") else str(response)
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            result = {
                "bug_detected": False,
                "bug_type": "unknown",
                "location": {"file": "buggy.py", "line": 0, "function": ""},
                "root_cause": "Failed to parse LLM response",
                "fix_description": content[:200],
                "confidence": "low",
                "parse_error": True,
            }

    elapsed = time.time() - start
    result["sample"] = sample_dir
    result["runtime_seconds"] = round(elapsed, 2)
    result["true_bug_type"] = metadata.get("bug_type", "")
    result["method"] = "pure_llm"

    # Validate: write fixed_code to buggy.py and run pytest
    test_path = d / "test_buggy.py"
    if result.get("bug_detected") and result.get("fixed_code") and test_path.exists():
        original_code = buggy_path.read_text()
        try:
            buggy_path.write_text(result["fixed_code"])
            pytest_result = subprocess.run(
                ["python", "-m", "pytest", str(test_path), "-q", "--tb=short"],
                capture_output=True, text=True, timeout=30, cwd=str(d),
            )
            all_passing = pytest_result.returncode == 0
            result["validation"] = {
                "all_passing": all_passing,
                "return_code": pytest_result.returncode,
                "stdout_tail": (pytest_result.stdout or "")[-500:],
                "stderr_tail": (pytest_result.stderr or "")[-500:],
            }
        except subprocess.TimeoutExpired:
            result["validation"] = {"all_passing": False, "error": "pytest timeout"}
        except Exception as e:
            result["validation"] = {"all_passing": False, "error": str(e)}
        finally:
            buggy_path.write_text(original_code)
    else:
        result["validation"] = {"all_passing": False, "reason": "no fixed_code or no bug detected"}

    return result


def main():
    parser = argparse.ArgumentParser(description="Pure LLM baseline for bug detection")
    parser.add_argument("--model", default="gpt-4o", help="LLM model to use")
    parser.add_argument("--provider", default="openai", help="LLM provider (openai, anthropic, deepseek)")
    parser.add_argument("--mock", action="store_true", help="Use mock responses (no API calls)")
    parser.add_argument("--dataset", help="Run on a specific dataset sample only")
    parser.add_argument(
        "--output", default=str(_REPORTS / "pure_llm_results.json"), help="Output file path"
    )
    args = parser.parse_args()

    if args.dataset:
        samples = [args.dataset]
    else:
        samples = sorted(
            [d.name for d in _DATASETS.iterdir() if d.is_dir() and (d / "buggy.py").exists()]
        )

    print(f"Running Pure LLM baseline on {len(samples)} samples...")
    print(f"Provider: {args.provider}, Model: {args.model}")
    if args.mock:
        print("[MOCK MODE] No API calls will be made.")

    results = []
    for i, sample in enumerate(samples):
        print(f"  [{i + 1}/{len(samples)}] {sample}...", end=" ", flush=True)
        r = run_pure_llm(sample, model=args.model, provider=args.provider, mock=args.mock)
        bug = "BUG" if r.get("bug_detected") else "OK"
        bug_type = r.get("bug_type", "?")
        print(f"{bug} ({bug_type}) in {r.get('runtime_seconds', 0)}s")
        results.append(r)

    output_path = Path(args.output)
    output_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to {output_path}")

    detected = sum(1 for r in results if r.get("bug_detected"))
    correct_type = sum(
        1 for r in results if r.get("bug_type") == r.get("true_bug_type")
    )
    all_passing = sum(1 for r in results if r.get("validation", {}).get("all_passing"))
    print(f"Detection rate: {detected}/{len(results)}")
    print(f"Type accuracy: {correct_type}/{len(results)}")
    print(f"Repair success rate: {all_passing}/{len(results)}")


if __name__ == "__main__":
    main()
