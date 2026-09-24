"""JSON-schema fingerprint. Local replacement for JsonSchemaGenerator.java.

Previously this called the external Express service (localhost:4000/schema).
Now it uses the local ``schema_generator`` module directly — no server needed.

Both sides of the whole-object body match (response subtrees in
``response_analyzer`` and request bodies in ``detectors``) go through this
function, so string equality of the canonical serialization is all that
matters for matching.
"""
from __future__ import annotations
import functools
import json

from .schema_generator import generate_schema


def _canonical(payload) -> str:
    return json.dumps(generate_schema(payload), sort_keys=True, separators=(",", ":"))


@functools.lru_cache(maxsize=4096)
def generate_json_schema(json_text: str) -> str:
    return _canonical(json.loads(json_text))
