"""LCS (Longest Common Substring) Matcher Approach for API Dependency Detection.

Implements field dependency detection using Longest Common Substring (LCS)
similarity with Porter stemming and identifier completion preprocessing,
matching the exact logic from the project source code (validator.py, lcs.py, detectors.py).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Ensure project modules are importable
_CURRENT_DIR = Path(__file__).resolve().parent
_SEARCH_PATHS = [
    _CURRENT_DIR,
    _CURRENT_DIR.parent,
]
for p in _SEARCH_PATHS:
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from app.lcs import lc_substr_norm
except ImportError:
    def lc_substr_norm(a: str, b: str) -> float:
        """Calculate normalized longest common substring similarity in [0.0, 1.0]."""
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if len(a) < len(b):
            a, b = b, a
        len_a, len_b = len(a), len(b)
        prev = [0] * (len_b + 1)
        max_len = 0
        for char_a in a:
            curr = [0] * (len_b + 1)
            for j, char_b in enumerate(b, start=1):
                if char_a == char_b:
                    curr[j] = prev[j - 1] + 1
                    if curr[j] > max_len:
                        max_len = curr[j]
            prev = curr
        return max_len / max(len_a, len_b)

try:
    from nltk.stem import PorterStemmer as _NLTKStemmer
    _NLTK = _NLTKStemmer()

    def stem(word: str) -> str:
        """Porter stemmer for word normalization."""
        return _NLTK.stem(word.lower())
except Exception:
    def _bundled_stem(word: str) -> str:
        """Lightweight rule-based suffix trimmer used as fallback when NLTK is absent."""
        w = word.lower()
        if len(w) <= 3:
            return w
        rules = (
            ("ization", "ize"), ("ational", "ate"), ("fulness", ""),
            ("ousness", ""), ("iveness", ""), ("tion", "t"),
            ("ness", ""), ("ment", ""), ("ing", ""), ("ed", ""),
            ("ies", "i"), ("es", ""), ("s", ""), ("ly", ""),
            ("er", ""), ("ic", ""), ("y", "i"),
        )
        for suffix, replacement in rules:
            if w.endswith(suffix) and len(w) - len(suffix) >= 3:
                return w[:-len(suffix)] + replacement
        return w

    def stem(word: str) -> str:
        return _bundled_stem(word)

logger = logging.getLogger(__name__)

LCS_THRESHOLD: float = 0.44


def id_completion_child(child: str, father: str) -> str:
    """Prefix child field name with stemmed parent name if child is short (<= 3 chars)."""
    if len(child) <= 3:
        return stem(father) + child
    return child


def id_completion_request(child: str, request_url: str) -> list[str]:
    """Prefix child field name with path segment hints from request URL if child is short."""
    if len(child) <= 3:
        segments = [s for s in urlparse(request_url).path.split("/") if s]
        if len(segments) >= 2:
            return [stem(segments[-1]) + child, stem(segments[-2]) + child]
        if len(segments) == 1:
            return [stem(segments[-1]) + child]
    return [child]


def name_match(
    to_name: str,
    atomic_name: str,
    father_name: str | None,
    request_url: str,
    threshold: float = LCS_THRESHOLD,
) -> bool:
    """Evaluate name similarity via Porter stemming and normalized longest common substring."""
    to_stemmed = stem(to_name.lower())
    atomic_lower = atomic_name.lower()

    if father_name:
        candidate = id_completion_child(atomic_lower, stem(father_name.lower()))
        return lc_substr_norm(to_stemmed, candidate) >= threshold

    for candidate in id_completion_request(atomic_lower, request_url):
        if lc_substr_norm(to_stemmed, stem(candidate)) >= threshold:
            return True

    return False


def lcs_url_match(
    atomic_name: str,
    from_url: str,
    father_name: str | None,
    possible_name: str,
    threshold: float = LCS_THRESHOLD,
) -> bool:
    """Evaluate dependency in URL path segment using LCS."""
    to_lower = possible_name.lower()
    atomic_lower = atomic_name.lower()

    if father_name:
        cand = id_completion_child(atomic_lower, stem(father_name.lower()))
        return lc_substr_norm(stem(to_lower), cand) >= threshold

    for cand in id_completion_request(atomic_lower, from_url):
        if lc_substr_norm(stem(to_lower), stem(cand)) >= threshold:
            return True

    return False


def match_fields(
    producer_endpoint: str,
    producer_field: str,
    producer_value: Any,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: Any,
    location: str = "body",
    producer_method: str = "",
    consumer_method: str = "",
    father_name: str | None = None,
    threshold: float = LCS_THRESHOLD,
) -> bool:
    """Evaluate API dependency between producer and consumer fields using LCS approach.

    Follows the exact pipeline of detectors.py and validator.py:
    1. Preprocessing:
       - Header Bearer auth check: if Authorization and Bearer, check value match directly.
       - Cookie auth check: if Authorization in cookie name, check value match directly.
       - Value equality: atomic.value == to_value.
    2. Field name preprocessing:
       - Suffix/parent extraction.
       - Porter stemming on consumer name.
       - Short identifier completion (URL segments or parent prefix).
    3. Similarity metric: Normalized longest common substring (LCS).
    4. Postprocessing: Threshold comparison (LCS_THRESHOLD = 0.44).
    """
    if not consumer_field:
        return False

    p_str = "" if producer_value is None else str(producer_value)
    c_str = "" if consumer_value is None else str(consumer_value)

    # Special header authorization rule matching evaluate_header_atomic_dep
    if location == "header" and consumer_field == "Authorization" and c_str.startswith("Bearer "):
        if producer_value is None or consumer_value is None:
            return True
        return p_str == c_str[7:]

    # Special cookie authorization rule matching evaluate_cookie_atomic_dep
    if location == "cookie" and "Authorization" in consumer_field:
        if producer_value is None or consumer_value is None:
            return True
        return p_str == c_str

    # General value equality check
    if producer_value is not None and consumer_value is not None:
        if p_str != c_str:
            return False

    # Extract father/parent if embedded in producer field name (e.g. 'user.id')
    atomic_name = producer_field
    if not father_name and "." in producer_field:
        father_name, atomic_name = producer_field.rsplit(".", 1)

    if location == "url":
        return lcs_url_match(
            atomic_name=atomic_name,
            from_url=producer_endpoint,
            father_name=father_name,
            possible_name=consumer_field,
            threshold=threshold,
        )

    return name_match(
        to_name=consumer_field,
        atomic_name=atomic_name,
        father_name=father_name,
        request_url=producer_endpoint,
        threshold=threshold,
    )


class LCSMatcher:
    """LCS Dependency Matcher class for running benchmark testcases."""

    def __init__(self, threshold: float = LCS_THRESHOLD):
        self.threshold = threshold
        self.name = "LCS"

    def match(self, testcase: dict[str, Any]) -> bool:
        """Run LCS approach on a benchmark testcase dictionary."""
        producer = testcase.get("producer", {})
        consumer = testcase.get("consumer", {})

        p_endpoint = producer.get("endpoint", "")
        p_field = producer.get("field", "")
        p_val = producer.get("value")
        p_method = producer.get("method", "")
        father_name = producer.get("father") or producer.get("parent")

        c_endpoint = consumer.get("endpoint", "")
        c_field = consumer.get("field", "")
        c_val = consumer.get("value")
        c_method = consumer.get("method", "")
        location = consumer.get("location", "body")

        return match_fields(
            producer_endpoint=p_endpoint,
            producer_field=p_field,
            producer_value=p_val,
            consumer_endpoint=c_endpoint,
            consumer_field=c_field,
            consumer_value=c_val,
            location=location,
            producer_method=p_method,
            consumer_method=c_method,
            father_name=father_name,
            threshold=self.threshold,
        )

    def predict(self, testcase: dict[str, Any]) -> bool:
        """Alias for match()."""
        return self.match(testcase)


# Convenience module-level functions
_DEFAULT_MATCHER = LCSMatcher()


def match(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the LCS approach."""
    return _DEFAULT_MATCHER.match(testcase)


def predict(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the LCS approach."""
    return _DEFAULT_MATCHER.predict(testcase)


def evaluate_testcase(testcase: dict[str, Any]) -> bool:
    """Alias for match()."""
    return _DEFAULT_MATCHER.match(testcase)