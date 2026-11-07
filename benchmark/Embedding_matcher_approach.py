"""Embedding Matcher Approach for API Dependency Detection.

Implements vector embedding-based field dependency detection using sentence
transformers, text normalization, and endpoint context formatting, matching
the exact logic from the project source code (embedding_matcher.py, validator.py).
"""

from __future__ import annotations

import logging
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

import numpy as np

# Ensure project modules are importable
_CURRENT_DIR = Path(__file__).resolve().parent
_SEARCH_PATHS = [
    _CURRENT_DIR,
    _CURRENT_DIR.parent,
]
for p in _SEARCH_PATHS:
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from LCS_matcher_approach import LCS_THRESHOLD, lcs_url_match, name_match

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD: float = 0.6
VALIDATOR_SCORE_THRESHOLD: float = 0.6
DEFAULT_EMBEDDING_MODEL: str = "BAAI/bge-small-en-v1.5"
SECONDARY_EMBEDDING_MODEL: str = "sentence-transformers/all-MiniLM-L6-v2"

try:
    from sentence_transformers import SentenceTransformer
    _HAS_SENTENCE_TRANSFORMERS = True
except ImportError:
    SentenceTransformer = None  # type: ignore[assignment,misc]
    _HAS_SENTENCE_TRANSFORMERS = False

COMMON_API_BASES = (
    "https://api.spotify.com/v1",
    "https://www.googleapis.com/drive/v3",
    "https://www.gitlab.com/api/v4",
    "https://example.com/api",
    "http://localhost:8080",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
)


@lru_cache(maxsize=4)
def _load_model(model_name: str) -> Any:
    """Load and cache SentenceTransformer model on CPU."""
    if not _HAS_SENTENCE_TRANSFORMERS or SentenceTransformer is None:
        raise ImportError("sentence-transformers package is required for embedding matching.")
    return SentenceTransformer(model_name, device="cpu")


def _normalize_text(text: str) -> str:
    """Convert camelCase and snake_case tokens into lowercase space-separated words."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _normalize_endpoint(endpoint: str, field_value: str | None = None) -> str:
    """Extract the last path segment and remove field_value if present."""
    cleaned = endpoint.strip()

    cleaned = re.sub(
        r"^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )

    for base in COMMON_API_BASES:
        normalized_base = base.rstrip("/")
        if cleaned.lower().startswith(normalized_base.lower()):
            cleaned = cleaned[len(normalized_base):]
            break

    if "://" in cleaned:
        cleaned = urlsplit(cleaned).path

    cleaned = cleaned.split("?", 1)[0].split("#", 1)[0]

    # Keep only the last non-empty path segment.
    parts = [part for part in cleaned.split("/") if part]
    cleaned = parts[-1] if parts else ""

    # Remove field_value if it exactly matches the endpoint segment.
    if field_value:
        field_value = str(field_value).strip()
        if cleaned == field_value:
            cleaned = parts[-2] if len(parts) > 1 else ""

    return _normalize_text(cleaned)


def _format_field_context(endpoint: str, field_name: str, field_value: str | None = None) -> str:
    """Format endpoint path and field name into textual prompt for embedding."""
    return f"API endpoint: {_normalize_endpoint(endpoint, field_value)}. Field name: {_normalize_text(field_name)}."


def _qualified_producer_name(field_name: str, father_name: str | None = None, xpath: str | None = None) -> str:
    """Construct full hierarchical field identifier for semantic matching."""
    if father_name:
        return f"{father_name}.{field_name}"
    if xpath:
        return f"{field_name} ({xpath})"
    return field_name


def field_match_embedding(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str = "",
    producer_value: str = "",
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    encoder_fn: Callable[[list[str]], np.ndarray] | None = None,
) -> tuple[bool, float]:
    """Compute semantic similarity score between producer and consumer fields."""
    norm_producer = _normalize_text(producer_field)
    norm_consumer = _normalize_text(consumer_field)

    if not norm_producer or not norm_consumer:
        return False, 0.0

    texts = [
        _format_field_context(producer_endpoint, producer_field),
        _format_field_context(consumer_endpoint, consumer_field, consumer_value),
    ]

    try:
        if encoder_fn is not None:
            embeddings = encoder_fn(texts)
        elif _HAS_SENTENCE_TRANSFORMERS and SentenceTransformer is not None:
            model = _load_model(model_name)
            # print(f"\n\n{texts[0]}\n{texts[1]}")
            embeddings = model.encode(
                texts,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        else:
            logger.debug("sentence-transformers not installed; skipping embedding inference.")
            return False, 0.0

        context_score = float(np.dot(embeddings[0], embeddings[1]))
        # print(f"Similarity: {context_score}")
        is_match = context_score >= similarity_threshold
        return is_match, context_score
    except Exception as exc:
        logger.warning("Embedding comparison failed: %s", exc)
        return False, 0.0


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
    xpath: str | None = None,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    validator_threshold: float = VALIDATOR_SCORE_THRESHOLD,
    fallback_to_lcs: bool = True,
    lcs_threshold: float = LCS_THRESHOLD,
    encoder_fn: Callable[[list[str]], np.ndarray] | None = None,
) -> bool:
    """Evaluate API dependency between fields using vector embeddings with LCS fallback.

    Pipeline matching validator._embedding_name_match:
    1. Preprocessing: Value verification and endpoint/field normalization.
       - Header Bearer auth check: if Authorization and Bearer, check value match directly.
       - Cookie auth check: if Authorization in cookie name, check value match directly.
       - Value equality: atomic.value == to_value.
    2. Context formatting: Format endpoint and field into contextual sentence.
    3. Vector embedding: Compute cosine similarity via sentence-transformers.
    4. Postprocessing: Check if match and score > validator_threshold (0.8).
    5. Fallback: If score <= 0.8, fall back to LCS matching (if fallback_to_lcs=True).
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

    atomic_name = producer_field
    if not father_name and "." in producer_field:
        father_name, atomic_name = producer_field.rsplit(".", 1)

    qualified_producer = _qualified_producer_name(atomic_name, father_name, xpath)
    formatted_producer_ep = f"{producer_method} {producer_endpoint}".strip()
    formatted_consumer_ep = f"{consumer_method} {consumer_endpoint}".strip()

    res, score = field_match_embedding(
        producer_endpoint=formatted_producer_ep,
        producer_field=qualified_producer,
        consumer_endpoint=formatted_consumer_ep,
        consumer_field=consumer_field,
        consumer_value=c_str,
        producer_value=p_str,
        model_name=model_name,
        similarity_threshold=similarity_threshold,
        encoder_fn=encoder_fn,
    )

    if res and score > validator_threshold:
        return True

    if fallback_to_lcs:
        if location == "url":
            return lcs_url_match(
                atomic_name=atomic_name,
                from_url=producer_endpoint,
                father_name=father_name,
                possible_name=consumer_field,
                threshold=lcs_threshold,
            )
        return name_match(
            to_name=consumer_field,
            atomic_name=atomic_name,
            father_name=father_name,
            request_url=producer_endpoint,
            threshold=lcs_threshold,
        )

    return False


class EmbeddingMatcher:
    """Embedding Dependency Matcher class for running benchmark testcases."""

    def __init__(
        self,
        model_name: str = SECONDARY_EMBEDDING_MODEL,
        similarity_threshold: float = SIMILARITY_THRESHOLD,
        validator_threshold: float = VALIDATOR_SCORE_THRESHOLD,
        fallback_to_lcs: bool = False,
        lcs_threshold: float = LCS_THRESHOLD,
        encoder_fn: Callable[[list[str]], np.ndarray] | None = None,
    ):
        self.model_name = model_name
        self.similarity_threshold = similarity_threshold
        self.validator_threshold = validator_threshold
        self.fallback_to_lcs = fallback_to_lcs
        self.lcs_threshold = lcs_threshold
        self.encoder_fn = encoder_fn
        self.name = "Embedding"

    def match(self, testcase: dict[str, Any]) -> bool:
        """Run Embedding approach on a benchmark testcase dictionary."""
        producer = testcase.get("producer", {})
        consumer = testcase.get("consumer", {})

        p_endpoint = producer.get("endpoint", "")
        p_field = producer.get("field", "")
        p_val = producer.get("value")
        p_method = producer.get("method", "")
        father_name = producer.get("father") or producer.get("parent")
        xpath = producer.get("xpath")

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
            xpath=xpath,
            model_name=self.model_name,
            similarity_threshold=self.similarity_threshold,
            validator_threshold=self.validator_threshold,
            fallback_to_lcs=self.fallback_to_lcs,
            lcs_threshold=self.lcs_threshold,
            encoder_fn=self.encoder_fn,
        )

    def predict(self, testcase: dict[str, Any]) -> bool:
        """Alias for match()."""
        return self.match(testcase)


# Convenience module-level functions
_DEFAULT_MATCHER = EmbeddingMatcher()


def match(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the Embedding approach."""
    return _DEFAULT_MATCHER.match(testcase)


def predict(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the Embedding approach."""
    return _DEFAULT_MATCHER.predict(testcase)


def evaluate_testcase(testcase: dict[str, Any]) -> bool:
    """Alias for match()."""
    return _DEFAULT_MATCHER.match(testcase)