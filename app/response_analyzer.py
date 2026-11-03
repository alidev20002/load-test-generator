"""Response flattening and structured decomposition into atomic fields."""

from __future__ import annotations

import json
from typing import Any

from . import schema_client
from .har_model import Header
from .response_model import AtomicObject, ResponseUnstructured, StructuredObject


def _serialize_json_fast(obj: Any) -> str:
    """Serialize object with compact separators for canonical JSON schema lookup."""
    return json.dumps(obj, separators=(",", ":"))


def _visit(obj: Any, xpath: str, out: list[Any]) -> None:
    """Recursively traverse dictionaries and lists, extracting atomic and structured objects."""
    if isinstance(obj, dict):
        for key, val in obj.items():
            if val is None:
                continue
            child_path = f"{xpath}.{key}"
            if isinstance(val, (str, int, float, bool)):
                out.append(AtomicObject(value=str(val), name=str(key), xpath=child_path))
            elif isinstance(val, dict):
                serialized = _serialize_json_fast(val)
                s = StructuredObject(
                    name=str(key),
                    value=serialized,
                    xpath=child_path,
                    schema_string=schema_client.generate_json_schema(serialized),
                )
                out.append(s)
                _visit(val, child_path, s.objects)
            elif isinstance(val, list):
                serialized = _serialize_json_fast(val)
                s = StructuredObject(
                    name=str(key),
                    value=serialized,
                    xpath=child_path,
                    schema_string=schema_client.generate_json_schema(serialized),
                )
                out.append(s)
                _iterate_array(val, child_path, str(key), s.objects)

    elif isinstance(obj, list):
        serialized = _serialize_json_fast(obj)
        s = StructuredObject(
            name="",
            value=serialized,
            xpath=xpath,
            schema_string=schema_client.generate_json_schema(serialized),
        )
        out.append(s)
        _iterate_array(obj, xpath, "", s.objects)


def _iterate_array(arr: list[Any], xpath: str, var_name: str, out: list[Any]) -> None:
    """Traverse array items, recording indexed atomic primitives and structured objects."""
    for idx, el in enumerate(arr):
        if el is None:
            continue
        if isinstance(el, (str, int, float, bool)):
            item_path = f"{xpath}.{var_name}[{idx}]" if var_name else f"{xpath}[{idx}]"
            out.append(AtomicObject(value=str(el), name=var_name, xpath=item_path))
        elif isinstance(el, dict):
            serialized = _serialize_json_fast(el)
            s = StructuredObject(
                name=var_name,
                value=serialized,
                xpath=f"{xpath}[{idx}]",
                schema_string=schema_client.generate_json_schema(serialized),
            )
            out.append(s)
            _visit(el, f"{xpath}[{idx}]", s.objects)


def get_unstructured_response(json_text: str) -> ResponseUnstructured:
    """Parse JSON response text into root 'All' structure and flattened fields."""
    response = ResponseUnstructured()
    if not json_text or not json_text.strip():
        return response

    cleaned = json_text.strip()
    response.objects.append(
        StructuredObject(
            name="All",
            value=cleaned,
            xpath="$",
            schema_string=schema_client.generate_json_schema(cleaned),
        )
    )

    try:
        parsed = json.loads(cleaned)
        _visit(parsed, "$", response.objects)
    except (json.JSONDecodeError, ValueError):
        pass

    return response


def analyze_response_headers(headers: list[Header], resp: ResponseUnstructured) -> None:
    """Extract dynamic cookie assignments from Set-Cookie headers."""
    for h in headers:
        if (h.name or "").lower() != "set-cookie":
            continue
        first_part = (h.value or "").split(";")[0].strip()
        if not first_part:
            continue
        parts = first_part.split("=", 1)
        name = parts[0].strip()
        if not name:
            continue

        value = parts[1].strip() if len(parts) == 2 else ""
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]

        resp.objects.append(
            AtomicObject(
                value=value,
                name=name,
                xpath=None,
                from_set_cookie=True,
            )
        )
