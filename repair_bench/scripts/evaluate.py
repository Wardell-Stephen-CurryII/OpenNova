"""Evaluation script: compare Pure LLM baseline vs OpenNova Model-Driven Repair.

Reads results from both methods and computes:
- Bug detection accuracy (precision, recall, F1)
- Bug type classification accuracy
- Repair success rate
- Output format compliance (valid JSON %)
- Average runtime

Usage:
    python repair_bench/scripts/evaluate.py
    python repair_bench/scripts/evaluate.py --pure-llm results_a.json --repair results_b.json
"""

import argparse
import json
import sys
from pathlib import Path

_REPAIR_BENCH = Path(__file__).resolve().parent.parent
_DATASETS = _REPAIR_BENCH / "datasets"
_REPORTS = _REPAIR_BENCH / "reports"
_REPORTS.mkdir(parents=True, exist_ok=True)

DEFAULT_PURE_LLM = _REPORTS / "pure_llm_results.json"
DEFAULT_REPAIR = _REPORTS / "opennova_repair_results.json"


def load_ground_truth() -> dict[str, dict]:
    """Load ground truth from all dataset metadata.json files."""
    gt = {}
    for d in sorted(_DATASETS.iterdir()):
        if d.is_dir() and (d / "metadata.json").exists():
            meta = json.loads((d / "metadata.json").read_text())
            gt[d.name] = {
                "bug_type": meta.get("bug_type", "unknown"),
                "has_bug": True,
            }
    return gt


def compute_metrics(results: list[dict], ground_truth: dict[str, dict]) -> dict:
    """Compute evaluation metrics for a set of results."""
    total = len(results)
    if total == 0:
        return {"error": "No results to evaluate"}

    tp = 0  # correctly detected bug
    fp = 0  # falsely detected bug
    fn = 0  # missed bug
    correct_type = 0
    json_valid = 0
    repair_success = 0
    total_runtime = 0.0
    repair_attempted = 0

    for r in results:
        sample = r.get("sample", "")
        gt = ground_truth.get(sample, {"bug_type": "unknown", "has_bug": True})

        # Detection
        detected = r.get("bug_detected", False)
        has_bug = gt["has_bug"]
        if detected and has_bug:
            tp += 1
        elif detected and not has_bug:
            fp += 1
        elif not detected and has_bug:
            fn += 1

        # Type accuracy
        if r.get("bug_type") == gt["bug_type"]:
            correct_type += 1

        # JSON validity (no parse_error flag)
        if not r.get("parse_error") and not r.get("error"):
            json_valid += 1

        # Repair success
        validation = r.get("validation", {})
        if validation:
            repair_attempted += 1
            if validation.get("all_passing"):
                repair_success += 1
        elif r.get("fix_description") and not r.get("parse_error"):
            repair_attempted += 1

        # Runtime
        total_runtime += r.get("runtime_seconds", 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

    return {
        "total_samples": total,
        "true_positives": tp,
        "false_positives": fp,
        "false_negatives": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1_score": round(f1, 4),
        "type_accuracy": round(correct_type / total, 4) if total > 0 else 0.0,
        "type_correct_count": correct_type,
        "json_validity_rate": round(json_valid / total, 4) if total > 0 else 0.0,
        "repair_success_rate": (
            round(repair_success / repair_attempted, 4) if repair_attempted > 0 else 0.0
        ),
        "repair_success_count": repair_success,
        "repair_attempted": repair_attempted,
        "avg_runtime_seconds": round(total_runtime / total, 2) if total > 0 else 0.0,
    }


def generate_comparison_table(
    pure_metrics: dict, repair_metrics: dict
) -> str:
    """Generate markdown comparison table."""
    lines = [
        "# Experiment Results: Pure LLM vs OpenNova Model-Driven Repair",
        "",
        "## Comparison Table",
        "",
        "| Metric | Pure LLM | OpenNova Repair | Winner |",
        "|---|---|---|---|",
    ]

    metrics = [
        ("Precision", "precision"),
        ("Recall", "recall"),
        ("F1 Score", "f1_score"),
        ("Type Classification Accuracy", "type_accuracy"),
        ("JSON Validity Rate", "json_validity_rate"),
        ("Repair Success Rate", "repair_success_rate"),
    ]

    for name, key in metrics:
        pure_val = pure_metrics.get(key, "N/A")
        repair_val = repair_metrics.get(key, "N/A")

        if isinstance(pure_val, float) and isinstance(repair_val, float):
            winner = "Pure LLM" if pure_val > repair_val else "OpenNova" if repair_val > pure_val else "Tie"
            pure_str = f"{pure_val:.2%}" if "rate" in key or "accuracy" in key else f"{pure_val:.4f}"
            repair_str = f"{repair_val:.2%}" if "rate" in key or "accuracy" in key else f"{repair_val:.4f}"
        else:
            pure_str = str(pure_val)
            repair_str = str(repair_val)
            winner = "-"

        lines.append(f"| {name} | {pure_str} | {repair_str} | {winner} |")

    # Runtime
    pure_rt = pure_metrics.get("avg_runtime_seconds", 0)
    repair_rt = repair_metrics.get("avg_runtime_seconds", 0)
    rt_winner = "Pure LLM" if pure_rt < repair_rt else "OpenNova" if repair_rt < pure_rt else "Tie"
    lines.append(
        f"| Avg Runtime (s) | {pure_rt:.2f} | {repair_rt:.2f} | {rt_winner} (faster) |"
    )

    # Sample counts
    lines.append("")
    lines.append(f"Pure LLM samples: {pure_metrics.get('total_samples', 0)}")
    lines.append(f"OpenNova Repair samples: {repair_metrics.get('total_samples', 0)}")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Evaluate repair experiment results")
    parser.add_argument(
        "--pure-llm", default=str(DEFAULT_PURE_LLM), help="Path to pure LLM results"
    )
    parser.add_argument(
        "--repair", default=str(DEFAULT_REPAIR), help="Path to OpenNova repair results"
    )
    parser.add_argument(
        "--output", default=str(_REPORTS / "comparison.md"), help="Output comparison report"
    )
    args = parser.parse_args()

    ground_truth = load_ground_truth()
    print(f"Loaded ground truth for {len(ground_truth)} samples")

    # Load pure LLM results
    pure_path = Path(args.pure_llm)
    if pure_path.exists():
        pure_results = json.loads(pure_path.read_text())
        pure_metrics = compute_metrics(pure_results, ground_truth)
        print(f"\nPure LLM ({len(pure_results)} samples):")
        print(f"  Precision: {pure_metrics['precision']:.4f}")
        print(f"  Recall: {pure_metrics['recall']:.4f}")
        print(f"  F1: {pure_metrics['f1_score']:.4f}")
        print(f"  Type accuracy: {pure_metrics['type_accuracy']:.2%}")
        print(f"  JSON valid: {pure_metrics['json_validity_rate']:.2%}")
        print(f"  Avg runtime: {pure_metrics['avg_runtime_seconds']}s")
    else:
        print(f"Pure LLM results not found at {pure_path}")
        pure_results = []
        pure_metrics = {"total_samples": 0}

    # Load repair results
    repair_path = Path(args.repair)
    if repair_path.exists():
        repair_results = json.loads(repair_path.read_text())
        repair_metrics = compute_metrics(repair_results, ground_truth)
        print(f"\nOpenNova Repair ({len(repair_results)} samples):")
        print(f"  Precision: {repair_metrics['precision']:.4f}")
        print(f"  Recall: {repair_metrics['recall']:.4f}")
        print(f"  F1: {repair_metrics['f1_score']:.4f}")
        print(f"  Type accuracy: {repair_metrics['type_accuracy']:.2%}")
        print(f"  JSON valid: {repair_metrics['json_validity_rate']:.2%}")
        print(f"  Repair success: {repair_metrics['repair_success_rate']:.2%}")
        print(f"  Avg runtime: {repair_metrics['avg_runtime_seconds']}s")
    else:
        print(f"OpenNova Repair results not found at {repair_path}")
        repair_results = []
        repair_metrics = {"total_samples": 0}

    # Generate comparison
    if pure_results and repair_results:
        table = generate_comparison_table(pure_metrics, repair_metrics)
        output_path = Path(args.output)
        output_path.write_text(table)
        print(f"\nComparison report saved to {output_path}")
        print("\n" + table)
    elif pure_results:
        print("\nOnly pure LLM results available. Run repair method for comparison.")
    elif repair_results:
        print("\nOnly repair results available. Run pure LLM baseline for comparison.")

    # Save combined metrics
    metrics_path = _REPORTS / "metrics.json"
    metrics_path.write_text(
        json.dumps(
            {
                "pure_llm": pure_metrics,
                "opennova_repair": repair_metrics,
                "ground_truth_samples": len(ground_truth),
            },
            indent=2,
        )
    )
    print(f"\nFull metrics saved to {metrics_path}")


if __name__ == "__main__":
    main()
