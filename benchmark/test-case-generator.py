#!/usr/bin/env python3
"""Generate reproducible producer-consumer API test cases from a project catalog.

Each catalog pair expands into five test cases, one per consumer location:
header, queryparam, body, url, and cookie. The --count argument is the
number of final cases to write and cannot exceed the catalog's total capacity.

Examples:
  python test-case-generator.py --catalog api_pairs.json --count 30 --seed 42
  python test-case-generator.py --catalog api_pairs.json --count 15 --positive-ratio 0.7 --seed 7
  python test-case-generator.py --catalog api_pairs.json --count 10 --projects spotify mastodon
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import uuid
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any
from urllib.parse import quote

LOCATIONS = ("header", "queryparam", "body", "url", "cookie")


def random_token(rng: random.Random, length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(rng.choice(alphabet) for _ in range(length))


def random_jwt_like(rng: random.Random) -> str:
    # Shape only; this is not a valid/signed JWT.
    return ".".join(random_token(rng, n) for n in (12, 20, 16))


def random_value(kind: str, rng: random.Random) -> str | bool:
    if kind == "token":
        return random_token(rng)
    if kind == "jwt":
        return random_jwt_like(rng)
    if kind == "uuid":
        return str(uuid.UUID(int=rng.getrandbits(128), version=4))
    if kind == "int":
        return str(rng.randint(1, 9999))
    if kind == "id":
        return str(rng.randint(1000, 9999))
    if kind == "string":
        return random_token(rng, 12)
    if kind == "bool":
        return rng.choice((True, False))
    raise ValueError(f"Unknown value generator: {kind!r}")


def safe_id_part(text: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_-]+", "_", text.strip())
    return text.strip("_") or "case"


def validate_endpoint(endpoint: str, where: str) -> None:
    if not isinstance(endpoint, str) or not endpoint.startswith("/"):
        raise ValueError(f"{where}.endpoint must be a path starting with '/' and must not include a base URL.")
    if endpoint.startswith("//") or "#" in endpoint:
        raise ValueError(f"{where}.endpoint must be a path only, without host or fragment.")


def validate_catalog(catalog: dict[str, Any]) -> None:
    projects = catalog.get("projects")
    if not isinstance(projects, dict) or not projects:
        raise ValueError("Catalog must contain a non-empty 'projects' object.")
    for project_name, project in projects.items():
        if not isinstance(project, dict):
            raise ValueError(f"Project {project_name!r} must be an object.")
        base_url = project.get("base_url")
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError(f"Project {project_name!r} must define a non-empty 'base_url'.")
        parsed_base_url = urlsplit(base_url)
        if parsed_base_url.scheme not in ("http", "https") or not parsed_base_url.netloc or parsed_base_url.query or parsed_base_url.fragment:
            raise ValueError(f"{project_name}.base_url must be an absolute http(s) URL without query or fragment.")
        for label_key in ("positive_pairs", "negative_pairs"):
            pairs = project.get(label_key, [])
            if not isinstance(pairs, list):
                raise ValueError(f"{project_name}.{label_key} must be a list.")
            for index, pair in enumerate(pairs):
                prefix = f"{project_name}.{label_key}[{index}]"
                for key in ("pattern", "producer", "consumer", "value_generator"):
                    if key not in pair:
                        raise ValueError(f"{prefix} is missing {key!r}.")
                for side in ("producer", "consumer"):
                    obj = pair[side]
                    for key in ("endpoint", "field"):
                        if key not in obj:
                            raise ValueError(f"{prefix}.{side} is missing {key!r}.")
                    validate_endpoint(obj["endpoint"], f"{prefix}.{side}")
                if "location" in pair["consumer"]:
                    raise ValueError(f"{prefix}.consumer must not define 'location'; locations are expanded automatically.")


def consumer_endpoint(path: str, field: str, value: str | bool, location: str) -> str:
    """Build the consumer endpoint according to the selected location."""
    value_text = str(value).lower() if isinstance(value, bool) else str(value)
    if location == "url":
        return f"{path.rstrip('/')}/{quote(field, safe='')}/{quote(value_text, safe='')}"
    if location == "queryparam":
        separator = "&" if "?" in path else "?"
        return f"{path}{separator}{quote(field, safe='')}={quote(value_text, safe='')}"
    return path


def build_case(
    project_name: str,
    base_url: str,
    pair: dict[str, Any],
    ground_truth: bool,
    rng: random.Random,
    case_number: int,
    location: str,
) -> dict[str, Any]:
    value = random_value(pair["value_generator"], rng)
    producer = dict(pair["producer"])
    consumer = dict(pair["consumer"])
    producer["value"] = value
    consumer["value"] = value
    consumer["location"] = location
    consumer_path = consumer_endpoint(consumer["endpoint"], consumer["field"], value, location)
    normalized_base_url = base_url.rstrip("/")
    producer["endpoint"] = f"{normalized_base_url}/{producer['endpoint'].lstrip('/')}"
    consumer["endpoint"] = f"{normalized_base_url}/{consumer_path.lstrip('/')}"

    pair_name = pair.get("name", pair.get("pattern", "case"))
    pair_pattern = pair.get("pattern", "Undefined Pattern")
    return {
        "id": f"GEN_{case_number:05d}_{safe_id_part(project_name)}_{safe_id_part(pair_pattern)}_{location}",
        "pattern": pair["pattern"],
        "producer": producer,
        "consumer": consumer,
        "note": pair.get("note", f"Generated from project catalog: {project_name}/{pair_name}"),
        "ground_truth": ground_truth,
    }


def build_pool(projects: dict[str, Any], allowed_projects: list[str] | None, label: bool):
    key = "positive_pairs" if label else "negative_pairs"
    names = allowed_projects if allowed_projects else list(projects)
    pool = []
    for project_name in names:
        if project_name not in projects:
            raise ValueError(f"Project {project_name!r} is not present in the catalog.")
        for pair in projects[project_name].get(key, []):
            for location in LOCATIONS:
                pool.append((project_name, projects[project_name]["base_url"], pair, location))
    return pool


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate seeded API dependency test cases.")
    parser.add_argument("--catalog", default="api_pairs.json", help="Path to project/pair catalog JSON.")
    parser.add_argument("--count", type=int, default=100, help="Number of final test cases (maximum: 5 per catalog pair).")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    parser.add_argument("--positive-ratio", type=float, default=0.5, help="Fraction of positive cases, from 0.0 to 1.0.")
    parser.add_argument("--out", default="generated_cases.json", help="Output JSON path.")
    parser.add_argument("--projects", nargs="*", default=None, help="Optional project names; default is all projects.")
    args = parser.parse_args()

    if args.count < 0:
        parser.error("--count must be >= 0")
    if not 0.0 <= args.positive_ratio <= 1.0:
        parser.error("--positive-ratio must be between 0.0 and 1.0")

    catalog_path = Path(args.catalog)
    try:
        with catalog_path.open("r", encoding="utf-8") as f:
            catalog = json.load(f)
        validate_catalog(catalog)
        projects = catalog["projects"]
        positive_pool = build_pool(projects, args.projects, True)
        negative_pool = build_pool(projects, args.projects, False)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))

    max_count = len(positive_pool) + len(negative_pool)
    if args.count > max_count:
        parser.error(
            f"--count={args.count} exceeds the maximum of {max_count} cases for the selected project(s). "
            f"Each catalog pair generates exactly {len(LOCATIONS)} location variants."
        )

    positive_count = round(args.count * args.positive_ratio)
    negative_count = args.count - positive_count
    if positive_count > len(positive_pool):
        parser.error(f"Requested {positive_count} positive cases, but only {len(positive_pool)} are available.")
    if negative_count > len(negative_pool):
        parser.error(f"Requested {negative_count} negative cases, but only {len(negative_pool)} are available.")

    rng = random.Random(args.seed)
    selected = [(item, True) for item in rng.sample(positive_pool, positive_count)]
    selected += [(item, False) for item in rng.sample(negative_pool, negative_count)]
    rng.shuffle(selected)

    cases = []
    by_project: dict[str, int] = {}
    for index, ((project_name, base_url, pair, location), label) in enumerate(selected, start=1):
        cases.append(build_case(project_name, base_url, pair, label, rng, index, location))
        by_project[project_name] = by_project.get(project_name, 0) + 1

    output_path = Path(args.out)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(cases, f, indent=2, ensure_ascii=False)
        f.write("\n")

    positive_total = sum(case["ground_truth"] is True for case in cases)
    print(f"Generated {len(cases)} test cases -> {output_path}")
    print(f"  maximum available: {max_count}")
    print(f"  positive: {positive_total}")
    print(f"  negative: {len(cases) - positive_total}")
    print(f"  seed: {args.seed}")
    print("  cases by project:")
    for name in sorted(by_project):
        print(f"    {name}: {by_project[name]}")


if __name__ == "__main__":
    main()
