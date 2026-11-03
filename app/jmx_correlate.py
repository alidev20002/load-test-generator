"""Inject dynamic correlation parameters and extractors into JMeter test plans."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from .graph import (
    CSVNode,
    DependencyGraph,
    Edge,
    MANUALLY_CSV,
    MANUALLY_INSERTED,
    MyNode,
)
from .har_model import Entry
from .jmx_base import should_skip
from .response_model import AtomicObject, StructuredObject

NOT_FOUND_SENTINEL = "NOT_FOUND"


def find_json_path(obj: Any, target_key: str, current_path: str = "$") -> str | None:
    """Find first JSON path leading to target key (e.g. $['data'][0]['token'])."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{current_path}['{k}']"
            if k == target_key:
                return path
            sub = find_json_path(v, target_key, path)
            if sub is not None:
                return sub
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            sub = find_json_path(item, target_key, f"{current_path}[{idx}]")
            if sub is not None:
                return sub
    return None


def bracket_to_dot(path: str) -> str:
    """Convert bracket notation ($['data'][0]['token']) to dot notation ($.data[0].token)."""
    result = "$"
    for match in re.finditer(r"\['([^]]+)'\]|\[(\d+)\]", path):
        if match.group(1) is not None:
            result += f".{match.group(1)}"
        else:
            result += f"[{match.group(2)}]"
    return result


def _parse_bracket_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for match in re.finditer(r"\['([^]]+)'\]|\[(\d+)\]", path):
        if match.group(1) is not None:
            tokens.append(match.group(1))
        else:
            tokens.append(int(match.group(2)))
    return tokens


def set_by_bracket_path(data_str: str, bracket_path: str, value: str) -> str:
    """Update value at target path in JSON string and re-serialize."""
    data = json.loads(data_str)
    node = data
    tokens = _parse_bracket_tokens(bracket_path)
    for token in tokens[:-1]:
        node = node[token]
    node[tokens[-1]] = value
    return json.dumps(data, separators=(",", ":"))


def _unquote_field(json_str: str, key: str, var_expr: str) -> str:
    """Unquote variable expression in JSON for structured object replacement."""
    return json_str.replace(f'"{key}":"{var_expr}"', f'"{key}":{var_expr}')


def _get_string_prop(elem: ET.Element, name: str) -> ET.Element | None:
    for child in elem:
        if child.tag == "stringProp" and child.get("name") == name:
            return child
    return None


def _find_thread_group_hashtree(root: ET.Element) -> ET.Element | None:
    for ht in root.iter("hashTree"):
        children = list(ht)
        for i, child in enumerate(children):
            if child.tag == "ThreadGroup" and i + 1 < len(children) and children[i + 1].tag == "hashTree":
                return children[i + 1]
    return None


def _get_ordered_samplers(parent_ht: ET.Element) -> list[tuple[ET.Element, ET.Element | None]]:
    children = list(parent_ht)
    samplers: list[tuple[ET.Element, ET.Element | None]] = []
    for i, child in enumerate(children):
        if child.tag == "HTTPSamplerProxy":
            sub_ht = children[i + 1] if i + 1 < len(children) and children[i + 1].tag == "hashTree" else None
            samplers.append((child, sub_ht))
    return samplers


def _build_var_ref(dep_name: str, from_idx: int, dep_value: str) -> str:
    if dep_value in (MANUALLY_INSERTED, MANUALLY_CSV):
        return dep_name
    return f"${{{dep_name}_{from_idx}}}"


def replace_url(sampler: ET.Element, deps: list[Edge], node: MyNode) -> None:
    """Replace static segments in sampler URL with correlation variable references."""
    path_elem = _get_string_prop(sampler, "HTTPSampler.path")
    try:
        parsed_url = urlparse(node.request.url)
    except Exception:
        return

    segments = parsed_url.path.split("/")
    changed = False

    for edge in deps:
        if edge.type != "url" or not edge.dependency:
            continue
        for idx, seg in enumerate(segments):
            if seg == edge.subPath:
                segments[idx] = _build_var_ref(edge.dependency.name, edge.from_index, edge.dependency.value)
                changed = True
                break

    if not changed or path_elem is None:
        return

    clean_segs = [s for s in segments if s]
    new_path = f"/{'/'.join(clean_segs)}" if clean_segs else "/"
    authority = parsed_url.hostname or ""
    if parsed_url.port:
        authority += f":{parsed_url.port}"

    new_full_url = f"{parsed_url.scheme}://{authority}{new_path}"
    if parsed_url.query:
        new_full_url += f"?{parsed_url.query}"
    if parsed_url.fragment:
        new_full_url += f"#{parsed_url.fragment}"

    path_elem.text = new_full_url


def replace_headers(sampler: ET.Element, deps: list[Edge]) -> None:
    """Update HeaderManager headers in sampler with correlation variables."""
    for prop in sampler.iter("elementProp"):
        if prop.get("name") != "HTTPSampler.header_manager":
            continue
        for h in prop.iter("elementProp"):
            hname = h.get("name", "")
            if (hname or "").lower() in ("cookie", "set-cookie"):
                continue
            for edge in deps:
                if edge.type != "header" or not edge.dependency or hname != edge.header_name:
                    continue
                var_ref = _build_var_ref(edge.dependency.name, edge.from_index, edge.dependency.value)
                if hname == "Authorization" and edge.dependency.value not in (MANUALLY_INSERTED, MANUALLY_CSV):
                    var_ref = f"Bearer {var_ref}"
                for sp in h:
                    if sp.tag == "stringProp" and sp.get("name") == "Header.value":
                        sp.text = var_ref


def _ensure_header_manager(sampler: ET.Element) -> tuple[ET.Element, ET.Element]:
    for prop in sampler:
        if prop.tag == "elementProp" and prop.get("name") == "HTTPSampler.header_manager":
            for coll in prop:
                if coll.tag == "collectionProp":
                    return prop, coll
    mgr = ET.Element("elementProp", {"name": "HTTPSampler.header_manager", "elementType": "HeaderManager"})
    coll = ET.SubElement(mgr, "collectionProp", {"name": "HeaderManager.headers"})
    sampler.append(mgr)
    return mgr, coll


def replace_cookies(sampler: ET.Element, deps: list[Edge]) -> None:
    """Inject Cookie header containing correlated cookie variables."""
    cookie_edges = [e for e in deps if e.type == "cookie" and e.dependency is not None]
    if not cookie_edges:
        return

    # Deduplicate cookies by name; last producer wins
    by_name: dict[str, Edge] = {}
    for edge in cookie_edges:
        key = edge.name or (edge.dependency.name if edge.dependency else "")
        by_name[key] = edge

    pairs = [
        f"{name}={_build_var_ref(edge.dependency.name, edge.from_index, edge.dependency.value)}"
        for name, edge in by_name.items()
        if edge.dependency is not None
    ]
    cookie_str = "; ".join(pairs)

    for prop in sampler.iter("elementProp"):
        if prop.get("name") != "HTTPSampler.header_manager":
            continue
        for h in prop.iter("elementProp"):
            if (h.get("name", "") or "").lower() == "cookie":
                for sp in h:
                    if sp.tag == "stringProp" and sp.get("name") == "Header.value":
                        sp.text = cookie_str
                return

    _, coll = _ensure_header_manager(sampler)
    header_elem = ET.SubElement(coll, "elementProp", {"name": "Cookie", "elementType": "Header"})
    name_sp = ET.SubElement(header_elem, "stringProp", {"name": "Header.name"})
    name_sp.text = "Cookie"
    val_sp = ET.SubElement(header_elem, "stringProp", {"name": "Header.value"})
    val_sp.text = cookie_str


def replace_args(sampler: ET.Element, deps: list[Edge]) -> None:
    """Replace URL query parameters and form body arguments with variable references."""
    for prop in sampler:
        if prop.tag != "elementProp" or prop.get("name") != "HTTPsampler.Arguments":
            continue
        for coll in prop:
            if coll.tag != "collectionProp":
                continue
            for arg in coll:
                if arg.tag != "elementProp":
                    continue
                arg_name = arg.get("name", "")
                for edge in deps:
                    matched = (
                        (edge.type == "queryparam" and arg_name == edge.query_param_name)
                        or (edge.type == "bodyue" and arg_name == edge.name)
                    )
                    if matched and edge.dependency:
                        for sp in arg:
                            if sp.tag == "stringProp" and sp.get("name") == "Argument.value":
                                sp.text = _build_var_ref(
                                    edge.dependency.name, edge.from_index, edge.dependency.value
                                )
                            if sp.tag == "boolProp" and sp.get("name") == "HTTPArgument.always_encode":
                                sp.text = "false"


def _get_body_value_elements(sampler: ET.Element) -> list[ET.Element]:
    result: list[ET.Element] = []
    for prop in sampler:
        if prop.tag != "elementProp" or prop.get("name") != "HTTPsampler.Arguments":
            continue
        for coll in prop:
            if coll.tag != "collectionProp":
                continue
            for arg in coll:
                if arg.tag != "elementProp":
                    continue
                for sp in arg:
                    if sp.tag == "stringProp" and sp.get("name") == "Argument.value":
                        result.append(sp)
    return result


def replace_json_body(
    sampler: ET.Element,
    sampler_ht: ET.Element | None,
    deps: list[Edge],
    node: MyNode,
) -> None:
    """Inject variables into JSON body or attach JSR223 Groovy preprocessor for complex merges."""
    body_edges = [e for e in deps if e.type == "bodyjson"]
    if not body_edges or not node.request.postData:
        return
    if "application/json" not in (node.request.postData.mimeType or ""):
        return

    targets = _get_body_value_elements(sampler)
    if not targets:
        return

    body_text = targets[0].text or ""
    whole_matches = [e for e in body_edges if not e.primitive and e.name == "All"]

    if whole_matches:
        if len(body_edges) == 1:
            edge = whole_matches[0]
            targets[0].text = f"${{{edge.structured.name}_{edge.from_index}}}" if edge.structured else body_text
        else:
            _add_groovy_preprocessor(sampler_ht, body_edges, node)
            targets[0].text = f"${{modifiedBody_{node.indexs}}}"
        return

    try:
        template = json.loads(node.request.postData.text or "{}")
    except Exception:
        template = {}

    for edge in body_edges:
        path = find_json_path(template, edge.name, "$")
        if path is None:
            continue
        if edge.primitive and edge.dependency:
            ref = _build_var_ref(edge.dependency.name, edge.from_index, edge.dependency.value)
            try:
                body_text = set_by_bracket_path(body_text, path, ref)
            except Exception:
                continue
        elif not edge.primitive and edge.structured:
            ref = f"${{{edge.structured.name}_{edge.from_index}}}"
            try:
                body_text = set_by_bracket_path(body_text, path, ref)
                body_text = _unquote_field(body_text, edge.name, ref)
            except Exception:
                continue

    targets[0].text = body_text


def _add_groovy_preprocessor(
    sampler_ht: ET.Element | None,
    bodies: list[Edge],
    node: MyNode,
) -> None:
    if sampler_ht is None:
        return

    prev_all = next((e.from_index for e in bodies if not e.primitive and e.name == "All"), bodies[0].from_index)
    lines = [
        "import com.jayway.jsonpath.JsonPath",
        "import com.jayway.jsonpath.DocumentContext",
        "",
        f'def originalJson = vars.get("All_{prev_all}")',
        "DocumentContext jsonDoc = JsonPath.parse(originalJson)",
        "def substitutions = [",
    ]

    try:
        template = json.loads(node.request.postData.text or "{}") if node.request.postData else {}
    except Exception:
        template = {}

    for edge in bodies:
        if not edge.primitive and edge.name == "All":
            continue
        arr_path = find_json_path(template, edge.name, "$")
        dot_path = bracket_to_dot(arr_path) if arr_path else f"$.{edge.name}"

        if edge.primitive and edge.dependency:
            if edge.dependency.value in (MANUALLY_INSERTED, MANUALLY_CSV):
                lines.append(f"[path:'{dot_path}', newValue:'{edge.dependency.name}'],")
            else:
                lines.append(f"[path:'{dot_path}', newValue:'${{{edge.dependency.name}_{edge.from_index}}}'],")
        elif not edge.primitive and edge.structured:
            lines.append(f"[path:'{dot_path}', newValue:'${{{edge.structured.name}_{edge.from_index}}}'],")

    lines.extend([
        "]",
        "substitutions.each { sub ->",
        "    jsonDoc.set(sub.path, sub.newValue)",
        "}",
        "String modifiedJson = jsonDoc.jsonString()",
        f'vars.put("modifiedBody_{node.indexs}", modifiedJson)',
    ])

    preprocessor = ET.Element(
        "JSR223PreProcessor",
        {
            "guiclass": "TestBeanGUI",
            "testclass": "JSR223PreProcessor",
            "testname": "Modify previously saved json response",
            "enabled": "true",
        },
    )

    for prop_name, prop_val in (
        ("scriptLanguage", "groovy"),
        ("parameters", ""),
        ("filename", ""),
        ("cacheKey", "true"),
    ):
        child = ET.SubElement(preprocessor, "stringProp", {"name": prop_name})
        child.text = prop_val

    script_elem = ET.SubElement(preprocessor, "stringProp", {"name": "script"})
    script_elem.text = "\n".join(lines)
    sampler_ht.append(preprocessor)


def add_json_extractor(sampler_ht: ET.Element, deps: list[Any]) -> None:
    """Inject a JSONPostProcessor into sampler hashTree for extracting dynamic values."""
    names: list[str] = []
    paths: list[str] = []

    for obj in deps:
        if isinstance(obj, AtomicObject):
            if obj.from_set_cookie:
                continue
            names.append(getattr(obj, "_ref_name", obj.name))
            paths.append(obj.xpath or "")
        elif isinstance(obj, StructuredObject):
            names.append(getattr(obj, "_ref_name", obj.name))
            paths.append(obj.xpath or "")

    if not names:
        return

    extractor = ET.Element(
        "JSONPostProcessor",
        {
            "guiclass": "JSONPostProcessorGui",
            "testclass": "JSONPostProcessor",
            "testname": "JSON Extractor",
            "enabled": "true",
        },
    )

    for prop_name, prop_val in (
        ("JSONPostProcessor.referenceNames", ";".join(names)),
        ("JSONPostProcessor.jsonPathExprs", ";".join(paths)),
        ("JSONPostProcessor.match_numbers", ""),
        ("JSONPostProcessor.defaultValues", ";".join([NOT_FOUND_SENTINEL] * len(names))),
    ):
        child = ET.SubElement(extractor, "stringProp", {"name": prop_name})
        child.text = prop_val

    sampler_ht.append(extractor)
    ET.SubElement(sampler_ht, "hashTree")


def add_cookie_regex_extractors(sampler_ht: ET.Element, cookie_deps: list[Any]) -> None:
    """Inject RegexExtractors targeting Set-Cookie response headers."""
    seen: set[str] = set()
    for obj in cookie_deps:
        if not isinstance(obj, AtomicObject) or not obj.from_set_cookie:
            continue
        ref = getattr(obj, "_ref_name", obj.name)
        if ref in seen:
            continue
        seen.add(ref)

        extractor = ET.Element(
            "RegexExtractor",
            {
                "guiclass": "RegexExtractorGui",
                "testclass": "RegexExtractor",
                "testname": f"Regex Extractor - cookie {obj.name}",
                "enabled": "true",
            },
        )

        for prop_name, prop_val in (
            ("RegexExtractor.useHeaders", "true"),
            ("RegexExtractor.refname", ref),
            ("RegexExtractor.regex", f"{obj.name}=([^;\\r\\n]+)"),
            ("RegexExtractor.template", "$1$"),
            ("RegexExtractor.default", NOT_FOUND_SENTINEL),
            ("RegexExtractor.match_number", "1"),
        ):
            child = ET.SubElement(extractor, "stringProp", {"name": prop_name})
            child.text = prop_val

        sampler_ht.append(extractor)
        ET.SubElement(sampler_ht, "hashTree")


def _build_csv_dataset_element(csv: CSVNode) -> ET.Element:
    elem = ET.Element(
        "CSVDataSet",
        {
            "guiclass": "TestBeanGUI",
            "testclass": "CSVDataSet",
            "testname": f"CSV Data Set Config {csv.filename}",
            "enabled": "true",
        },
    )
    props = (
        ("filename", csv.filename, False),
        ("fileEncoding", csv.encoding, False),
        ("variableNames", csv.variablesName, False),
        ("ignoreFirstLine", str(csv.ignorefirstLine).lower(), True),
        ("delimiter", ",", False),
        ("quotedData", "false", True),
        ("recycle", "true", True),
        ("stopThread", "false", True),
        ("shareMode", "shareMode.all", False),
    )
    for name, val, is_bool in props:
        tag = "boolProp" if is_bool else "stringProp"
        child = ET.SubElement(elem, tag, {"name": name})
        child.text = val

    return elem


def correlate(
    tree: ET.ElementTree,
    graph: DependencyGraph,
    entries: list[Entry],
) -> ET.ElementTree:
    """Correlate base JMX test plan using dynamic dependencies identified in graph."""
    root = tree.getroot()
    tg_hashtree = _find_thread_group_hashtree(root)
    if tg_hashtree is None:
        raise RuntimeError("ThreadGroup hashTree could not be located in base JMX")

    for csv in graph.csv_deps():
        tg_hashtree.insert(0, ET.Element("hashTree"))
        tg_hashtree.insert(0, _build_csv_dataset_element(csv))

    filtered_entries = [
        (i, e) for i, e in enumerate(entries)
        if not should_skip(e.request.url) and not (e.request.url or "").lower().startswith(("ws://", "wss://"))
    ]

    samplers = _get_ordered_samplers(tg_hashtree)
    if len(samplers) != len(filtered_entries):
        raise RuntimeError(f"Sampler mismatch: {len(samplers)} samplers vs {len(filtered_entries)} HAR entries")

    for (har_idx, entry), (sampler, sht) in zip(filtered_entries, samplers):
        node = graph.get_node_by_index(har_idx)
        if node is None:
            node = graph.get_node_by_url_method(entry.request.url, entry.request.method)
        if node is None:
            continue

        deps = graph.deps_by_node(node)
        replace_url(sampler, deps, node)
        replace_headers(sampler, deps)
        replace_cookies(sampler, deps)
        replace_args(sampler, deps)
        replace_json_body(sampler, sht, deps, node)

        extracted = graph.deps_to_extract(node, har_idx)
        for obj in extracted:
            obj._ref_name = f"{obj.name}_{har_idx}"  # type: ignore[attr-defined]

        if extracted and sht is not None:
            cookie_deps = [o for o in extracted if isinstance(o, AtomicObject) and o.from_set_cookie]
            add_json_extractor(sht, extracted)
            if cookie_deps:
                add_cookie_regex_extractors(sht, cookie_deps)

    return tree
