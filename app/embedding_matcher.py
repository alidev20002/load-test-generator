"""Vector embedding matcher for detecting semantic similarity between API fields."""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from pathlib import Path
import numpy as np

from sentence_transformers import SentenceTransformer
from huggingface_hub import snapshot_download

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.6
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


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

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models" / "all-MiniLM-L6-v2"

REQUIRED_FILES = [
    "model.safetensors",
    "modules.json",
    "config.json",
    "config_sentence_transformers.json",
    "sentence_bert_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "1_Pooling/config.json",
]

@lru_cache(maxsize=4)
def _load_all_minilm_model():
    missing_files = [
        f for f in REQUIRED_FILES
        if not (MODEL_DIR / f).is_file()
    ]

    if missing_files:
        print("Downloading original model files...")

        snapshot_download(
            repo_id=MODEL_NAME,
            local_dir=str(MODEL_DIR),
            allow_patterns=REQUIRED_FILES,
        )

    print("Loading local model...")

    return SentenceTransformer(
        str(MODEL_DIR.resolve()),
        device="cpu",
        local_files_only=True,
    )


def _normalize_text(text: str) -> str:
    """Convert camelCase and snake_case tokens into lowercase space-separated words."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _normalize_endpoint(endpoint: str, field_value: str | None = None) -> str:
    """Strip HTTP method, host prefix, query parameters and all garbage parts from endpoint URL."""
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


def field_match_embedding(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str = "",
    producer_value: str = "",
    model_name: str = DEFAULT_EMBEDDING_MODEL,
) -> tuple[bool | None, float]:
    """Compute semantic similarity score between producer and consumer fields."""

    norm_producer = _normalize_text(producer_field)
    norm_consumer = _normalize_text(consumer_field)

    if not norm_producer or not norm_consumer:
        return False, 0.0

    try:
        model = _load_all_minilm_model()
        texts = [
            _format_field_context(producer_endpoint, producer_field),
            _format_field_context(consumer_endpoint, consumer_field, consumer_value),
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
        return None, 0.0
