"""Vector embedding matcher for detecting semantic similarity between API fields."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit

import numpy as np

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"

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


def _normalize_endpoint(endpoint: str) -> str:
    """Strip HTTP method, host prefix, and query parameters from endpoint URL."""
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
    return _normalize_text(cleaned)


def _format_field_context(endpoint: str, field_name: str) -> str:
    """Format endpoint path and field name into textual prompt for embedding."""
    return f"API endpoint: {_normalize_endpoint(endpoint)}. Field name: {_normalize_text(field_name)}."


def field_match_embedding(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str = "",
    producer_value: str = "",
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> tuple[bool, float]:
    """Compute semantic similarity score between producer and consumer fields."""
    if not _HAS_SENTENCE_TRANSFORMERS:
        logger.debug("sentence-transformers not installed; skipping embedding match.")
        return False, 0.0

    norm_producer = _normalize_text(producer_field)
    norm_consumer = _normalize_text(consumer_field)

    if not norm_producer or not norm_consumer:
        return False, 0.0

    try:
        model = _load_model(model_name)
        texts = [
            _format_field_context(producer_endpoint, producer_field),
            _format_field_context(consumer_endpoint, consumer_field),
        ]

        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        context_score = float(np.dot(embeddings[0], embeddings[1]))
        is_match = context_score >= SIMILARITY_THRESHOLD
        return is_match, context_score
    except Exception as exc:
        logger.warning("Embedding comparison failed: %s", exc)
        return False, 0.0
