"""Canonical JSON-schema fingerprinting and caching for structural matching."""

from __future__ import annotations

import functools
import json
from typing import Any

from .schema_generator import generate_schema


def serialize_canonical_schema(payload: Any) -> str:
    """Generate and serialize a canonical JSON schema fingerprint."""
    schema = generate_schema(payload)
    return json.dumps(schema, sort_keys=True, separators=(",", ":"))


@functools.lru_cache(maxsize=4096)
def generate_json_schema(json_text: str) -> str:
    """Parse JSON text and return its canonical schema fingerprint string."""
    try:
        payload = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return serialize_canonical_schema(payload)
