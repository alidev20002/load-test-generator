
import json
import argparse
import time
from collections import defaultdict
from urllib.parse import urlparse

from nltk.stem.porter import PorterStemmer

from llm_matcher_sample import field_match, LLMConfig


LCS_THRESHOLD = 0.44

LLM_ENDPOINT = "http://192.168.10.47:11434"
LLM_MODEL = "qwen2.5vl:7b"

_stemmer = PorterStemmer()


def porter_stem(word: str) -> str:
    return _stemmer.stem(word)


def lcs_ratio(name1: str, name2: str) -> float:
    name1, name2 = name1.lower(), name2.lower()
    m, n = len(name1), len(name2)

    if m == 0 or n == 0:
        return 0.0

    dp = [[0] * (n + 1) for _ in range(m + 1)]
    best = 0

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if name1[i - 1] == name2[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
                best = max(best, dp[i][j])

    return best / max(m, n)


def id_completion_url_fallback(child: str, producer_url: str):
    if len(child) > 3:
        return [child]

    path = urlparse(producer_url).path
    subpath = path.split("/")

    while len(subpath) < 2:
        subpath = [""] + subpath

    last = subpath[-1] if subpath[-1] else subpath[-2]
    second_last = subpath[-2] if len(subpath) > 1 else ""

    return [
        porter_stem(last) + child,
        porter_stem(second_last) + child,
    ]


def value_gate(case: dict) -> bool:
    producer = case["producer"]
    consumer = case["consumer"]
    loc = consumer["location"]

    if loc.lower() == "header" and consumer["field"] == "Authorization":
        cval = consumer["value"]
        cval = cval[7:] if len(cval) > 7 else cval
        return producer["value"] == cval

    if loc.lower() == "cookie" and "Authorization" in consumer["field"]:
        return producer["value"] == consumer["value"]

    return producer["value"] == consumer["value"]


def name_gate(case: dict) -> bool:
    producer = case["producer"]
    consumer = case["consumer"]

    consumer_name = porter_stem(consumer["field"].lower())
    candidates = id_completion_url_fallback(
        producer["field"].lower(),
        producer["endpoint"],
    )

    for candidate in candidates:
        candidate_stem = porter_stem(candidate)

        if lcs_ratio(consumer_name, candidate_stem) >= LCS_THRESHOLD:
            return True

    return False


def e2eloaderpp_predict(case: dict) -> bool:
    return value_gate(case) and name_gate(case)


def llm_predict(case: dict, cfg: LLMConfig) -> bool | None:
    return field_match(
        producer_endpoint=case["producer"]["endpoint"],
        producer_field=case["producer"]["field"],
        producer_value=case["producer"]["value"],
        consumer_endpoint=case["consumer"]["endpoint"],
        consumer_field=case["consumer"]["field"],
        consumer_value=case["consumer"]["value"],
        location=case["consumer"]["location"],
        cfg=cfg,
    )


def calculate_metrics(cases: list[dict], predictions: list[bool | None]):
    """Calculate binary metrics; exclude None from confusion-matrix counts."""
    tp = fp = tn = fn = invalid = 0

    for case, pred in zip(cases, predictions):
        gt = case["ground_truth"]

        if pred is None:
            invalid += 1
            continue

        if pred == gt:
            if gt:
                tp += 1
            else:
                tn += 1
        else:
            if pred:
                fp += 1
            else:
                fn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    valid = tp + fp + tn + fn
    correct = tp + tn
    total = len(cases)

    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "invalid": invalid,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "accuracy": correct / valid if valid else 0.0,
        "valid": valid,
        "total": total,
        # Invalid predictions are incorrect for overall benchmark accuracy.
        "overall_accuracy": correct / total if total else 0.0,
    }


def print_report(
    method_name: str,
    cases: list[dict],
    predictions: list[bool | None],
):
    metrics = calculate_metrics(cases, predictions)

    print(f"\n{'=' * 65}")
    print(f"{method_name} - Evaluation Metrics")
    print("=" * 65)

    print(f"TP        : {metrics['tp']}")
    print(f"FP        : {metrics['fp']}")
    print(f"TN        : {metrics['tn']}")
    print(f"FN        : {metrics['fn']}")
    print(f"Invalid   : {metrics['invalid']}")
    print(f"Precision : {metrics['precision']:.4f}")
    print(f"Recall    : {metrics['recall']:.4f}")
    print(f"F1        : {metrics['f1']:.4f}")

    print(
        f"Accuracy (valid predictions): "
        f"{metrics['accuracy']:.4f} "
        f"({metrics['tp'] + metrics['tn']}/{metrics['valid']})"
    )
    print(
        f"Overall accuracy: "
        f"{metrics['overall_accuracy']:.4f} "
        f"({metrics['tp'] + metrics['tn']}/{metrics['total']})"
    )

    # Count incorrect predictions by pattern.
    pattern_errors = defaultdict(
        lambda: {"total": 0, "fp": 0, "fn": 0, "invalid": 0}
    )

    for case, pred in zip(cases, predictions):
        pattern = case.get("pattern", "UNKNOWN")
        gt = case["ground_truth"]

        if pred is None:
            pattern_errors[pattern]["total"] += 1
            pattern_errors[pattern]["invalid"] += 1
        elif pred != gt:
            pattern_errors[pattern]["total"] += 1

            if pred is True:
                pattern_errors[pattern]["fp"] += 1
            else:
                pattern_errors[pattern]["fn"] += 1

    print(f"\n{method_name} - Errors by Pattern")
    print("-" * 65)
    print(
        f"{'Pattern':<28} {'Errors':>8} "
        f"{'FP':>8} {'FN':>8} {'Invalid':>9}"
    )
    print("-" * 65)

    if not pattern_errors:
        print("No patterns found.")
        return

    for pattern, counts in sorted(
        pattern_errors.items(),
        key=lambda item: (-item[1]["total"], item[0]),
    ):
        print(
            f"{pattern:<28} "
            f"{counts['total']:>8} "
            f"{counts['fp']:>8} "
            f"{counts['fn']:>8} "
            f"{counts['invalid']:>9}"
        )

    print("-" * 65)
    print(
        f"{'TOTAL':<28} "
        f"{sum(x['total'] for x in pattern_errors.values()):>8} "
        f"{sum(x['fp'] for x in pattern_errors.values()):>8} "
        f"{sum(x['fn'] for x in pattern_errors.values()):>8} "
        f"{sum(x['invalid'] for x in pattern_errors.values()):>9}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark E2E-Loader++ LCS against LLM field matching."
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--lcs-only",
        action="store_true",
        help="Run only E2E-Loader++ (LCS).",
    )
    mode.add_argument(
        "--llm-only",
        action="store_true",
        help="Run only the LLM.",
    )

    parser.add_argument(
        "--cases",
        default="generated_cases.json",
        help="Path to the generated test cases JSON file.",
    )
    parser.add_argument("--model", default=LLM_MODEL)
    parser.add_argument("--endpoint", default=LLM_ENDPOINT)

    args = parser.parse_args()

    with open(args.cases, "r", encoding="utf-8") as f:
        cases = json.load(f)

    run_lcs = not args.llm_only
    run_llm = not args.lcs_only

    cfg = LLMConfig(
        provider="ollama",
        endpoint=args.endpoint,
        model=args.model,
        fallback_to_lcs=False,
        cache_enabled=False,
    )

    print(f"Test cases : {len(cases)}")
    print(
        "Mode       : "
        f"{'LCS only' if args.lcs_only else 'LLM only' if args.llm_only else 'LCS + LLM'}"
    )

    if run_llm:
        print(f"LLM model  : {cfg.model}")
        print(f"Endpoint   : {cfg.endpoint}")

    print()

    header = f"{'ID':<32} {'GT':<8}"

    if run_lcs:
        header += f"{'LCS':<8} "

    if run_llm:
        header += f"{'LLM':<8} "

    if run_lcs:
        header += f"{'LCS_OK':<8} "

    if run_llm:
        header += f"{'LLM_OK':<8}"

    print(header)
    print("-" * len(header))

    lcs_predictions = []
    llm_predictions = []
    llm_failures = []

    benchmark_start = time.perf_counter()

    for case in cases:
        gt = case["ground_truth"]
        row = f"{case['id']:<32} {str(gt):<8}"

        if run_lcs:
            lcs_pred = e2eloaderpp_predict(case)
            lcs_predictions.append(lcs_pred)
            lcs_ok = lcs_pred == gt
            row += f"{str(lcs_pred):<8} "

        if run_llm:
            try:
                llm_pred = llm_predict(case, cfg)
            except Exception as exc:
                llm_pred = None
                llm_failures.append((case["id"], str(exc)))

            llm_predictions.append(llm_pred)
            llm_ok = llm_pred == gt
            row += f"{str(llm_pred):<8} "

        if run_lcs:
            row += f"{'OK' if lcs_ok else 'X':<8} "

        if run_llm:
            row += f"{'OK' if llm_ok else 'X':<8}"

        print(row)

    elapsed = time.perf_counter() - benchmark_start

    print("-" * len(header))

    if run_lcs:
        print_report("E2E-Loader++ (LCS)", cases, lcs_predictions)

    if run_llm:
        print_report(f"LLM ({cfg.model})", cases, llm_predictions)

    print(f"\nBenchmark time: {elapsed:.2f} seconds")

    if cases:
        print(f"Average time per case: {elapsed / len(cases):.3f} seconds")

    if llm_failures:
        print(f"\nLLM request failures: {len(llm_failures)}")

        for case_id, error in llm_failures:
            print(f"  - {case_id}: {error}")


if __name__ == "__main__":
    main()
