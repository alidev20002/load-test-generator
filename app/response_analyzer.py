"""Response flattening. Mirrors Services/ResponseAnalyzer/ResponseAnalyzer.java."""
from __future__ import annotations
from .har_model import Header
from .response_model import AtomicObject, StructuredObject, ResponseUnstructured
from . import schema_client


def _new_structured(name: str, value: str, xpath: str) -> StructuredObject:
    return StructuredObject(name=name, value=value, xpath=xpath,
                            schema_string=schema_client.generate_json_schema(value))


def _visit(obj, xpath: str, out: list) -> None:
    if isinstance(obj, dict):
        for key, val in obj.items():
            if val is None:
                continue
            if isinstance(val, (str, int, float, bool)):
                out.append(AtomicObject(value=str(val), name=str(key), xpath=f"{xpath}.{key}"))
            elif isinstance(val, dict):
                import json
                s = StructuredObject(name=str(key), value=json.dumps(val, separators=(",", ":")),
                                     xpath=f"{xpath}.{key}",
                                     schema_string=schema_client.generate_json_schema(
                                         json.dumps(val, separators=(",", ":"))))
                out.append(s)
                _visit(val, f"{xpath}.{key}", s.objects)
            elif isinstance(val, list):
                import json
                s = StructuredObject(name=str(key), value=json.dumps(val, separators=(",", ":")),
                                     xpath=f"{xpath}.{key}",
                                     schema_string=schema_client.generate_json_schema(
                                         json.dumps(val, separators=(",", ":"))))
                out.append(s)
                _iterate_array(val, f"{xpath}.{key}", str(key), s.objects)
    elif isinstance(obj, list):
        import json
        s = StructuredObject(name="", value=json.dumps(obj, separators=(",", ":")),
                             xpath=xpath,
                             schema_string=schema_client.generate_json_schema(
                                 json.dumps(obj, separators=(",", ":"))))
        out.append(s)
        _iterate_array(obj, xpath, "", s.objects)


def _iterate_array(arr: list, xpath: str, var_name: str, out: list) -> None:
    import json
    for i, el in enumerate(arr):
        if el is None:
            continue
        if isinstance(el, (str, int, float, bool)):
            out.append(AtomicObject(value=str(el), name=var_name, xpath=f"{xpath}.{var_name}[{i}]"))
        elif isinstance(el, dict):
            s = StructuredObject(name=var_name, value=json.dumps(el, separators=(",", ":")),
                                 xpath=f"{xpath}[{i}]",
                                 schema_string=schema_client.generate_json_schema(
                                     json.dumps(el, separators=(",", ":"))))
            out.append(s)
            _visit(el, f"{xpath}[{i}]", s.objects)


def get_unstructured_response(json_text: str) -> ResponseUnstructured:
    """Port of getUnstructuredResponse: root 'All' node + recursive flatten."""
    import json
    r = ResponseUnstructured()
    if not json_text or not json_text.strip():
        return r
    t = json_text.strip()
    r.objects.append(StructuredObject(name="All", value=json_text, xpath="$",
                                      schema_string=schema_client.generate_json_schema(json_text)))
    try:
        if t.startswith("{"):
            _visit(json.loads(t), "$", r.objects)
        elif t.startswith("["):
            _visit(json.loads(t), "$", r.objects)
    except Exception:
        pass  # Java would throw; be tolerant for non-JSON bodies
    return r


def analyze_response_headers(headers: list[Header], resp: ResponseUnstructured) -> None:
    """Port of analyzeResponseHeader: Set-Cookie -> AtomicObject(from_set_cookie=True)."""
    for h in headers:
        if h.name == "Set-Cookie":
            first = h.value.split(";")[0]
            parts = first.split("=", 1)
            if len(parts) == 2:
                resp.objects.append(AtomicObject(value=parts[1], name=parts[0], xpath=None,
                                                 from_set_cookie=True))
            elif parts:
                resp.objects.append(AtomicObject(value="", name=parts[0], xpath=None,
                                                 from_set_cookie=True))
