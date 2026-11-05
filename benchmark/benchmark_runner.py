"""Benchmark Runner for API Field Dependency Detection Approaches.

Evaluates and compares LCS, Embedding, and LLM matching approaches on a
benchmark dataset of testcases, computing Precision, Recall, F1-Score,
Accuracy, Specificity, TP, FP, TN, FN, latency, and per-pattern metrics.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure project modules are importable
_CURRENT_DIR = Path(__file__).resolve().parent
_SEARCH_PATHS = [
    _CURRENT_DIR,
    _CURRENT_DIR.parent,
]
for p in _SEARCH_PATHS:
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

import Embedding_matcher_approach
import LCS_matcher_approach
import LLM_matcher_approach

logger = logging.getLogger("benchmark_runner")


@dataclass
class PatternMetrics:
    """Metrics for a specific testcase pattern category."""
    pattern: str
    total: int = 0
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * (p * r) / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        return (self.tp + self.tn) / self.total if self.total > 0 else 0.0


@dataclass
class BenchmarkResult:
    """Evaluation metrics and prediction details for a single matching approach."""
    approach_name: str
    total: int = 0
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    total_time_seconds: float = 0.0
    avg_time_per_case_ms: float = 0.0
    pattern_breakdown: dict[str, PatternMetrics] = field(default_factory=dict)
    case_results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def precision(self) -> float:
        """Precision = TP / (TP + FP)."""
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) > 0 else 0.0

    @property
    def recall(self) -> float:
        """Recall (Sensitivity) = TP / (TP + FN)."""
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) > 0 else 0.0

    @property
    def f1(self) -> float:
        """F1-Score = 2 * (Precision * Recall) / (Precision + Recall)."""
        p, r = self.precision, self.recall
        return 2 * (p * r) / (p + r) if (p + r) > 0 else 0.0

    @property
    def accuracy(self) -> float:
        """Accuracy = (TP + TN) / Total."""
        return (self.tp + self.tn) / self.total if self.total > 0 else 0.0

    @property
    def specificity(self) -> float:
        """Specificity (True Negative Rate) = TN / (TN + FP)."""
        return self.tn / (self.tn + self.fp) if (self.tn + self.fp) > 0 else 0.0

    @property
    def error_rate(self) -> float:
        """Error Rate = (FP + FN) / Total."""
        return (self.fp + self.fn) / self.total if self.total > 0 else 0.0

    @property
    def balanced_accuracy(self) -> float:
        """Balanced Accuracy = (Recall + Specificity) / 2."""
        return (self.recall + self.specificity) / 2.0

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for export."""
        return {
            "approach": self.approach_name,
            "total_testcases": self.total,
            "true_positives": self.tp,
            "false_positives": self.fp,
            "true_negatives": self.tn,
            "false_negatives": self.fn,
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1_score": round(self.f1, 4),
            "accuracy": round(self.accuracy, 4),
            "specificity": round(self.specificity, 4),
            "balanced_accuracy": round(self.balanced_accuracy, 4),
            "error_rate": round(self.error_rate, 4),
            "total_time_seconds": round(self.total_time_seconds, 4),
            "avg_time_per_case_ms": round(self.avg_time_per_case_ms, 2),
            "pattern_breakdown": {
                k: {
                    "total": v.total,
                    "tp": v.tp,
                    "fp": v.fp,
                    "tn": v.tn,
                    "fn": v.fn,
                    "precision": round(v.precision, 4),
                    "recall": round(v.recall, 4),
                    "f1": round(v.f1, 4),
                    "accuracy": round(v.accuracy, 4),
                }
                for k, v in self.pattern_breakdown.items()
            },
        }


class BenchmarkRunner:
    """Engine for benchmarking dependency matching approaches against testcase datasets."""

    def __init__(self):
        self._matchers: dict[str, Any] = {
            "LCS": LCS_matcher_approach.LCSMatcher(),
            "Embedding": Embedding_matcher_approach.EmbeddingMatcher(),
            "LLM": LLM_matcher_approach.LLMMatcher(),
        }

    def register_matcher(self, name: str, matcher_instance: Any) -> None:
        """Register or override a matcher approach instance."""
        self._matchers[name] = matcher_instance

    @staticmethod
    def load_testcases(file_path: str | Path) -> list[dict[str, Any]]:
        """Load testcases from JSON file, supporting list or wrapped dictionary."""
        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Testcases file not found: {path.resolve()}")

        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("testcases", "test_cases", "tests", "data"):
                if key in data and isinstance(data[key], list):
                    return data[key]
            # Single testcase object
            if "producer" in data and "consumer" in data:
                return [data]

        raise ValueError(f"Unable to parse testcases from {path}. Expected JSON list or dictionary with 'testcases'.")

    def resolve_approaches(self, approach_names: list[str] | None) -> list[str]:
        """Resolve and validate requested approach names."""
        if not approach_names:
            return list(self._matchers.keys())

        resolved: list[str] = []
        for name in approach_names:
            clean = name.strip()
            if not clean:
                continue

            if clean.lower() == "all":
                return list(self._matchers.keys())

            match_found = False
            for registered_name in self._matchers:
                if clean.lower() == registered_name.lower():
                    resolved.append(registered_name)
                    match_found = True
                    break

            if not match_found:
                # Common aliases
                if clean.lower() in ("emb", "vector", "embedding"):
                    resolved.append("Embedding")
                elif clean.lower() in ("lcs", "longest_common_substring"):
                    resolved.append("LCS")
                elif clean.lower() in ("llm", "ai", "model"):
                    resolved.append("LLM")
                else:
                    available = ", ".join(self._matchers.keys()) + ", all"
                    raise ValueError(f"Unknown approach '{clean}'. Available approaches: {available}")

        return list(dict.fromkeys(resolved))

    def evaluate_approach(self, approach_name: str, testcases: list[dict[str, Any]]) -> BenchmarkResult:
        """Evaluate a single approach against the testcases and compute metrics."""
        matcher = self._matchers[approach_name]
        result = BenchmarkResult(approach_name=approach_name, total=len(testcases))

        patterns: dict[str, PatternMetrics] = {}
        case_results: list[dict[str, Any]] = []

        start_time = time.perf_counter()

        for idx, tc in enumerate(testcases, start=1):
            tc_id = tc.get("id", f"case_{idx}")
            pattern = str(tc.get("pattern", "default"))
            ground_truth = bool(tc.get("ground_truth", False))

            if pattern not in patterns:
                patterns[pattern] = PatternMetrics(pattern=pattern)
            pat_metric = patterns[pattern]
            pat_metric.total += 1

            tc_start = time.perf_counter()
            try:
                prediction = bool(matcher.predict(tc))
            except Exception as exc:
                logger.error("Error predicting case %s with %s: %s", tc_id, approach_name, exc)
                prediction = False
            tc_duration_ms = (time.perf_counter() - tc_start) * 1000.0

            # Confusion matrix accumulation
            if ground_truth and prediction:
                result.tp += 1
                pat_metric.tp += 1
                outcome = "TP"
            elif not ground_truth and prediction:
                result.fp += 1
                pat_metric.fp += 1
                outcome = "FP"
            elif not ground_truth and not prediction:
                result.tn += 1
                pat_metric.tn += 1
                outcome = "TN"
            else:  # ground_truth and not prediction
                result.fn += 1
                pat_metric.fn += 1
                outcome = "FN"

            case_results.append({
                "id": tc_id,
                "pattern": pattern,
                "ground_truth": ground_truth,
                "predicted": prediction,
                "outcome": outcome,
                "duration_ms": round(tc_duration_ms, 2),
                "producer_field": tc.get("producer", {}).get("field", ""),
                "consumer_field": tc.get("consumer", {}).get("field", ""),
                "location": tc.get("consumer", {}).get("location", ""),
            })

        total_elapsed = time.perf_counter() - start_time
        result.total_time_seconds = total_elapsed
        result.avg_time_per_case_ms = (total_elapsed / len(testcases) * 1000.0) if testcases else 0.0
        result.pattern_breakdown = patterns
        result.case_results = case_results

        return result

    def run(
        self,
        testcases_path: str | Path,
        approaches: list[str] | None = None,
    ) -> dict[str, BenchmarkResult]:
        """Run benchmark on testcases from file path for specified approaches."""
        testcases = self.load_testcases(testcases_path)
        resolved_approaches = self.resolve_approaches(approaches)

        results: dict[str, BenchmarkResult] = {}
        for app_name in resolved_approaches:
            results[app_name] = self.evaluate_approach(app_name, testcases)

        return results

    @staticmethod
    def print_results(
        results: dict[str, BenchmarkResult],
        dataset_name: str = "",
        show_case_details: bool = False,
    ) -> None:
        """Pretty-print benchmark results and metrics to console."""
        print("\n" + "=" * 90)
        print(" " * 28 + "API DEPENDENCY DETECTION BENCHMARK")
        print("=" * 90)

        if dataset_name:
            print(f"Dataset: {dataset_name}")
        first_res = next(iter(results.values())) if results else None
        if first_res:
            gt_pos = first_res.tp + first_res.fn
            gt_neg = first_res.tn + first_res.fp
            print(f"Total Testcases: {first_res.total} (Ground Truth Positives: {gt_pos}, Negatives: {gt_neg})")
        print("-" * 90)

        # Overview Table
        headers = ["Approach", "Total", "TP", "FP", "TN", "FN", "Accuracy", "Precision", "Recall", "F1-Score", "Time (s)"]
        header_fmt = "{:<12} {:>6} {:>5} {:>5} {:>5} {:>5} {:>9} {:>10} {:>8} {:>9} {:>9}"
        print(header_fmt.format(*headers))
        print("-" * 90)

        for name, r in results.items():
            print(header_fmt.format(
                name,
                r.total,
                r.tp,
                r.fp,
                r.tn,
                r.fn,
                f"{r.accuracy:.4f}",
                f"{r.precision:.4f}",
                f"{r.recall:.4f}",
                f"{r.f1:.4f}",
                f"{r.total_time_seconds:.2f}",
            ))
        print("-" * 90)

        # Detailed breakdown per approach
        for name, r in results.items():
            print(f"\n[ Detailed Report: {name} ]")
            print(f"  • True Positives  (TP) : {r.tp:<5}   |   Precision        : {r.precision:.4%}")
            print(f"  • False Positives (FP) : {r.fp:<5}   |   Recall (Sens.)   : {r.recall:.4%}")
            print(f"  • True Negatives  (TN) : {r.tn:<5}   |   Specificity      : {r.specificity:.4%}")
            print(f"  • False Negatives (FN) : {r.fn:<5}   |   F1-Score         : {r.f1:.4%}")
            print(f"  • Total Accuracy       : {r.accuracy:.4%}  |   Balanced Acc.    : {r.balanced_accuracy:.4%}")
            print(f"  • Total Time           : {r.total_time_seconds:.3f}s   |   Avg / Case       : {r.avg_time_per_case_ms:.2f}ms")

            # Confusion Matrix
            print("\n  Confusion Matrix:")
            print("                 Predicted Pos    Predicted Neg")
            print(f"    Actual Pos   TP = {r.tp:<11} FN = {r.fn:<11}")
            print(f"    Actual Neg   FP = {r.fp:<11} TN = {r.tn:<11}")

            # Per-pattern breakdown if available
            has_patterns = any(k != "default" for k in r.pattern_breakdown)
            if has_patterns:
                print("\n  Per-Pattern Breakdown:")
                pat_header_fmt = "    {:<35} {:>5} {:>4} {:>4} {:>4} {:>4} {:>8} {:>8} {:>8}"
                print(pat_header_fmt.format("Pattern", "Total", "TP", "FP", "TN", "FN", "Prec.", "Recall", "F1"))
                print("    " + "-" * 84)
                for pat_name, pm in sorted(r.pattern_breakdown.items()):
                    print(pat_header_fmt.format(
                        pat_name[:35],
                        pm.total,
                        pm.tp,
                        pm.fp,
                        pm.tn,
                        pm.fn,
                        f"{pm.precision:.2f}",
                        f"{pm.recall:.2f}",
                        f"{pm.f1:.2f}",
                    ))

            # Show misclassifications if requested
            mismatches = [c for c in r.case_results if c["outcome"] in ("FP", "FN")]
            if show_case_details and mismatches:
                print(f"\n  Misclassified Testcases ({len(mismatches)} cases):")
                for m in mismatches[:15]:
                    print(f"    - [{m['outcome']}] ID: {m['id']} (Pattern: {m['pattern']})")
                    print(f"        Producer: '{m['producer_field']}' -> Consumer: '{m['consumer_field']}' ({m['location']})")
                    print(f"        Ground Truth: {m['ground_truth']}, Predicted: {m['predicted']}")
                if len(mismatches) > 15:
                    print(f"        ... and {len(mismatches) - 15} more misclassified cases.")

        print("\n" + "=" * 90 + "\n")

    @staticmethod
    def export_json(results: dict[str, BenchmarkResult], output_path: str | Path) -> None:
        """Export benchmark metrics and testcase results to JSON."""
        out = {
            "summary": {name: r.to_dict() for name, r in results.items()},
            "case_predictions": {
                name: r.case_results for name, r in results.items()
            },
        }
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"Benchmark results successfully exported to JSON: {path.resolve()}")

    @staticmethod
    def export_csv(results: dict[str, BenchmarkResult], output_path: str | Path) -> None:
        """Export benchmark summary metrics to CSV."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Approach", "Total", "TP", "FP", "TN", "FN",
                "Accuracy", "Precision", "Recall", "F1_Score",
                "Specificity", "Error_Rate", "Total_Time_Sec", "Avg_Latency_ms",
            ])
            for name, r in results.items():
                writer.writerow([
                    name, r.total, r.tp, r.fp, r.tn, r.fn,
                    round(r.accuracy, 4), round(r.precision, 4), round(r.recall, 4), round(r.f1, 4),
                    round(r.specificity, 4), round(r.error_rate, 4),
                    round(r.total_time_seconds, 4), round(r.avg_time_per_case_ms, 2),
                ])
        print(f"Benchmark summary exported to CSV: {path.resolve()}")


def run_benchmark(
    testcases_path: str | Path,
    approaches: list[str] | str | None = None,
    verbose: bool = True,
    output_path: str | Path | None = None,
    show_case_details: bool = False,
) -> dict[str, BenchmarkResult]:
    """Programmatic API to run benchmark and return results."""
    runner = BenchmarkRunner()

    if isinstance(approaches, str):
        approaches = [a.strip() for a in approaches.replace(",", " ").split() if a.strip()]

    results = runner.run(testcases_path, approaches=approaches)

    if verbose:
        runner.print_results(
            results,
            dataset_name=str(testcases_path),
            show_case_details=show_case_details,
        )

    if output_path:
        out_str = str(output_path)
        if out_str.endswith(".csv"):
            runner.export_csv(results, output_path)
        else:
            runner.export_json(results, output_path)

    return results


def main() -> None:
    """CLI entry point for benchmark_runner."""
    parser = argparse.ArgumentParser(
        description="Benchmark runner for API field dependency detection approaches (LCS, Embedding, LLM).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python benchmark_runner.py testcases.json
  python benchmark_runner.py testcases.json LCS
  python benchmark_runner.py testcases.json LCS Embedding
  python benchmark_runner.py testcases.json LCS Embedding LLM
  python benchmark_runner.py -f testcases.json -a LCS,Embedding -o results.json
  python benchmark_runner.py testcases.json all --show-details
""",
    )

    parser.add_argument(
        "testcases",
        nargs="?",
        default=None,
        help="Path to JSON testcases file.",
    )
    parser.add_argument(
        "approaches",
        nargs="*",
        default=None,
        help="One or more approaches to evaluate: LCS, Embedding, LLM, or 'all'.",
    )
    parser.add_argument(
        "-f", "--file", "--testcases-file",
        dest="flag_testcases",
        default=None,
        help="Alternative flag for path to JSON testcases file.",
    )
    parser.add_argument(
        "-a", "--approaches-flag",
        dest="flag_approaches",
        default=None,
        help="Comma or space-separated list of approaches (e.g. 'LCS,Embedding,LLM').",
    )
    parser.add_argument(
        "-o", "--output",
        default=None,
        help="Path to save benchmark report (.json or .csv).",
    )
    parser.add_argument(
        "--show-details",
        action="store_true",
        help="Display individual misclassified testcase details.",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress console output (useful when exporting).",
    )

    args = parser.parse_args()

    target_file = args.flag_testcases or args.testcases
    if not target_file:
        parser.print_help()
        sys.exit(1)

    approaches_list: list[str] = []
    if args.flag_approaches:
        approaches_list.extend(args.flag_approaches.replace(",", " ").split())
    if args.approaches:
        approaches_list.extend(args.approaches)

    if not approaches_list:
        approaches_list = ["all"]

    try:
        run_benchmark(
            testcases_path=target_file,
            approaches=approaches_list,
            verbose=not args.quiet,
            output_path=args.output,
            show_case_details=args.show_details,
        )
    except Exception as exc:
        print(f"\n[ERROR] Benchmark execution failed: {exc}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()