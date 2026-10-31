
import re
from urllib.parse import urlsplit
from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer

THRESHOLD = 0.75
MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_NAME_2 = "BAAI/bge-small-en-v1.5"


@lru_cache(maxsize=4)
def _load_model(model_name=MODEL_NAME_2):
    return SentenceTransformer(
        model_name,
        device="cpu",
        local_files_only=True
    )

BASE_URLS = [
    "https://api.spotify.com/v1",
    "https://www.googleapis.com/drive/v3",
    "https://www.gitlab.com/api/v4",
    "https://example.com/api",
    "http://localhost:8080",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://127.0.0.1:8080",
]


def _normalize(text: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"([A-Z])([A-Z][a-z])", r"\1 \2", text)

    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)

    return " ".join(text.split())


def _normalize_endpoint(endpoint: str, field_value: str) -> str:
    endpoint = endpoint.strip()

    # Remove optional HTTP method, e.g. GET /users/{userId}.
    endpoint = re.sub(
        r"^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+",
        "",
        endpoint,
        flags=re.IGNORECASE,
    )

    # Remove only a matching base URL from the beginning.
    for base_url in BASE_URLS:
        base = base_url.rstrip("/")

        if endpoint.lower().startswith(base.lower()):
            endpoint = endpoint[len(base):]
            break

    # Remove query string and fragment, if present.
    if "://" in endpoint:
        parsed = urlsplit(endpoint)
        endpoint = parsed.path

    # Remove query parameters and everything after '?'.
    endpoint = endpoint.split("?", 1)[0]

    # Remove fragment.
    endpoint = endpoint.split("#", 1)[0]

    return _normalize(endpoint)

def _field_text(endpoint: str, field: str, value: str) -> str:
    return (
        f"API endpoint: {_normalize_endpoint(endpoint=endpoint, field_value=value)}. "
        f"Field name: {_normalize(field)}."
    )


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    denominator = np.linalg.norm(a) * np.linalg.norm(b)

    if denominator == 0:
        return 0.0

    return float(np.dot(a, b) / denominator)


def field_match_embedding(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str = "",
    producer_value: str = "",
    model_name: str = MODEL_NAME_2,
) -> bool:
    producer_name = _normalize(producer_field)
    consumer_name = _normalize(consumer_field)

    if not producer_name or not consumer_name:
        return False

    # Exact normalized field names are a strong lexical signal.
    # if producer_name == consumer_name:
    #     return True

    model = _load_model(model_name=model_name)

    texts = [
        _field_text(producer_endpoint, producer_field, producer_value),
        _field_text(consumer_endpoint, consumer_field, consumer_value),
        producer_name,
        consumer_name,
    ]

    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    )

    # Semantic similarity of the complete endpoint + field context.
    context_score = float(np.dot(vectors[0], vectors[1]))

    print(f"context score between: {texts[0]} and {texts[1]} is {context_score}")

    return context_score >= THRESHOLD
