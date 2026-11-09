"""Generate JSON schema structures from JSON-serializable payloads."""

from __future__ import annotations

from typing import Any

VALID_SCHEMA_TYPES = {"string", "number", "array", "object", "boolean", "integer"}


def _infer_primitive_type(val: Any) -> str:
    """Infer the JSON schema primitive type for a given scalar value."""
    if val is None or val is False or val == "" or val == 0:
        return "string"
    if isinstance(val, str):
        return val if val in VALID_SCHEMA_TYPES else "string"
    if isinstance(val, bool):
        return "boolean"
    if isinstance(val, (int, float)):
        return "number"
    return "object"


def is_schema_node(obj: Any) -> bool:
    """Check if a dictionary already represents a JSON schema definition."""
    if not isinstance(obj, dict):
        return False
    
    schema_type = obj.get("type")
    return isinstance(schema_type, str) and schema_type in VALID_SCHEMA_TYPES


def _parse_into_schema(data: Any, schema: dict[str, Any]) -> None:
    """Recursively parse Python data into a JSON schema dictionary."""
    if isinstance(data, list):
        schema["type"] = "array"
        items_schema: dict[str, Any] = {}
        schema["items"] = items_schema
        _parse_into_schema(data[0] if data else None, items_schema)

    elif isinstance(data, dict):
        if is_schema_node(data):
            schema.update(data)
            if schema.get("type") == "object":
                schema.pop("properties", None)
                _parse_into_schema(data.get("properties"), schema)
            elif schema.get("type") == "array":
                schema.pop("items", None)
                items_schema = {}
                schema["items"] = items_schema
                _parse_into_schema(data.get("items"), items_schema)
            return

        schema["type"] = "object"
        required_keys: list[str] = []
        schema["required"] = required_keys
        properties: dict[str, Any] = {}
        schema["properties"] = properties

        for raw_key, value in data.items():
            if str(raw_key).startswith("*"):
                key = str(raw_key)[1:]
                required_keys.append(key)
            else:
                key = str(raw_key)

            child_schema: dict[str, Any] = {}
            properties[key] = child_schema
            _parse_into_schema(value, child_schema)

    else:
        schema["type"] = _infer_primitive_type(data)


def generate_schema(payload: Any) -> dict[str, Any]:
    """Generate a canonical JSON schema structure for any payload object."""
    schema: dict[str, Any] = {}
    _parse_into_schema(payload, schema)
    return schema
