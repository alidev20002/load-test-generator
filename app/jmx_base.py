"""Base Apache JMeter test plan (.jmx) builder from HTTP Archive (HAR) entries."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

from .har_model import Entry

EXCLUDED_HEADERS = {
    "host",
    "content-length",
    "connection",
    "accept-encoding",
    "transfer-encoding",
    "cookie",
    "set-cookie",
}

STATIC_ASSET_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".ico",
    ".svg",
    ".gif",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".css",
    ".js",
)


def should_skip(url: str) -> bool:
    """Determine whether URL points to a static asset that should be skipped."""
    url_lower = url.lower()
    if "/static/" in url_lower:
        return True
    clean_path = url_lower.split("?")[0]
    return clean_path.endswith(STATIC_ASSET_EXTENSIONS)


def _add_string_prop(parent: ET.Element, name: str, text: str) -> ET.Element:
    elem = ET.SubElement(parent, "stringProp", {"name": name})
    elem.text = text
    return elem


def _add_bool_prop(parent: ET.Element, name: str, text: str) -> ET.Element:
    elem = ET.SubElement(parent, "boolProp", {"name": name})
    elem.text = text
    return elem


def _build_empty_arguments() -> ET.Element:
    args = ET.Element("elementProp", {"name": "HTTPsampler.Arguments", "elementType": "Arguments"})
    ET.SubElement(args, "collectionProp", {"name": "Arguments.arguments"})
    return args


def _build_query_arguments(query: str) -> ET.Element:
    args = ET.Element("elementProp", {"name": "HTTPsampler.Arguments", "elementType": "Arguments"})
    coll = ET.SubElement(args, "collectionProp", {"name": "Arguments.arguments"})
    for key, val in parse_qsl(query, keep_blank_values=True):
        arg = ET.SubElement(coll, "elementProp", {"name": key, "elementType": "HTTPArgument"})
        _add_bool_prop(arg, "HTTPArgument.always_encode", "true")
        _add_string_prop(arg, "Argument.name", key)
        _add_string_prop(arg, "Argument.value", val)
        _add_string_prop(arg, "Argument.metadata", "=")
        _add_bool_prop(arg, "HTTPArgument.use_equals", "true")
    return args


def _build_body_arguments(body: str) -> ET.Element:
    args = ET.Element("elementProp", {"name": "HTTPsampler.Arguments", "elementType": "Arguments"})
    coll = ET.SubElement(args, "collectionProp", {"name": "Arguments.arguments"})
    arg = ET.SubElement(coll, "elementProp", {"name": "", "elementType": "HTTPArgument"})
    _add_bool_prop(arg, "HTTPArgument.always_encode", "false")
    _add_string_prop(arg, "Argument.value", body)
    _add_string_prop(arg, "Argument.metadata", "=")
    _add_bool_prop(arg, "HTTPArgument.use_equals", "true")
    return args


def _build_header_manager(headers: list) -> ET.Element | None:
    prop = ET.Element(
        "elementProp",
        {"name": "HTTPSampler.header_manager", "elementType": "HeaderManager"},
    )
    coll = ET.SubElement(prop, "collectionProp", {"name": "HeaderManager.headers"})
    count = 0
    for h in headers:
        if not h.name or h.name.lower() in EXCLUDED_HEADERS:
            continue
        hp = ET.SubElement(coll, "elementProp", {"name": h.name, "elementType": "Header"})
        _add_string_prop(hp, "Header.name", h.name)
        _add_string_prop(hp, "Header.value", h.value)
        count += 1
    return prop if count > 0 else None


def _build_http_sampler(entry: Entry) -> ET.Element | None:
    url, method = entry.request.url, entry.request.method
    if should_skip(url):
        return None

    sampler = ET.Element(
        "HTTPSamplerProxy",
        {
            "guiclass": "HttpTestSampleGui",
            "testclass": "HTTPSamplerProxy",
            "testname": url,
            "enabled": "true",
        },
    )

    try:
        parsed_url = urlparse(url)
        _add_string_prop(sampler, "HTTPSampler.protocol", parsed_url.scheme or "http")
        _add_string_prop(sampler, "HTTPSampler.domain", parsed_url.hostname or "")
        _add_string_prop(sampler, "HTTPSampler.port", str(parsed_url.port) if parsed_url.port else "")
        _add_string_prop(sampler, "HTTPSampler.path", parsed_url.path or "/")
        sampler.append(_build_query_arguments(parsed_url.query) if parsed_url.query else _build_empty_arguments())
    except Exception:
        _add_string_prop(sampler, "HTTPSampler.path", url)
        sampler.append(_build_empty_arguments())

    _add_string_prop(sampler, "HTTPSampler.method", method)
    _add_bool_prop(sampler, "HTTPSampler.follow_redirects", "true")
    _add_bool_prop(sampler, "HTTPSampler.auto_redirects", "false")
    _add_bool_prop(sampler, "HTTPSampler.use_keepalive", "true")
    _add_bool_prop(sampler, "HTTPSampler.DO_MULTIPART_POST", "false")

    if method in ("POST", "PUT", "PATCH") and entry.request.postData is not None:
        _add_bool_prop(sampler, "HTTPSampler.postBodyRaw", "true")
        sampler.append(_build_body_arguments(entry.request.postData.text or ""))

    header_mgr = _build_header_manager(entry.request.headers)
    if header_mgr is not None:
        sampler.append(header_mgr)

    return sampler


def _build_test_plan() -> ET.Element:
    tp = ET.Element(
        "TestPlan",
        {
            "guiclass": "TestPlanGui",
            "testclass": "TestPlan",
            "testname": "Load Test Plan",
            "enabled": "true",
        },
    )
    _add_string_prop(tp, "TestPlan.comments", "Automated load test plan generated from HAR recording")
    _add_bool_prop(tp, "TestPlan.functional_mode", "false")
    _add_bool_prop(tp, "TestPlan.serialize_threadgroups", "false")

    args = ET.SubElement(
        tp,
        "elementProp",
        {
            "name": "TestPlan.user_defined_variables",
            "elementType": "Arguments",
            "guiclass": "ArgumentsPanel",
            "testclass": "Arguments",
            "testname": "User Defined Variables",
            "enabled": "true",
        },
    )
    ET.SubElement(args, "collectionProp", {"name": "Arguments.arguments"})
    return tp


def _build_thread_group() -> ET.Element:
    tg = ET.Element(
        "ThreadGroup",
        {
            "guiclass": "ThreadGroupGui",
            "testclass": "ThreadGroup",
            "testname": "Thread Group",
            "enabled": "true",
        },
    )
    _add_string_prop(tg, "ThreadGroup.on_sample_error", "continue")

    loop = ET.SubElement(
        tg,
        "elementProp",
        {
            "name": "ThreadGroup.main_controller",
            "elementType": "LoopController",
            "guiclass": "LoopControllerPanel",
            "testclass": "LoopController",
            "testname": "Loop Controller",
            "enabled": "true",
        },
    )
    _add_bool_prop(loop, "LoopController.continue_forever", "false")
    _add_string_prop(loop, "LoopController.loops", "1")
    _add_string_prop(tg, "ThreadGroup.num_threads", "1")
    _add_string_prop(tg, "ThreadGroup.ramp_time", "1")
    _add_bool_prop(tg, "ThreadGroup.scheduler", "false")
    _add_string_prop(tg, "ThreadGroup.duration", "")
    _add_string_prop(tg, "ThreadGroup.delay", "")
    return tg


def build_base_doc(entries: list[Entry]) -> ET.ElementTree:
    """Build the base JMeter Test Plan XML tree from valid HTTP entries."""
    root = ET.Element("jmeterTestPlan", {"version": "1.2", "properties": "5.0", "jmeter": "5.4.3"})
    ht = ET.SubElement(root, "hashTree")
    ht.append(_build_test_plan())

    tht = ET.SubElement(ht, "hashTree")
    tht.append(_build_thread_group())

    ght = ET.SubElement(tht, "hashTree")
    hm = ET.Element(
        "HeaderManager",
        {
            "guiclass": "HeaderPanel",
            "testclass": "HeaderManager",
            "testname": "HTTP Header Manager",
            "enabled": "true",
        },
    )
    ET.SubElement(hm, "collectionProp", {"name": "HeaderManager.headers"})
    ght.append(hm)
    ET.SubElement(ght, "hashTree")

    for entry in entries:
        if (entry.request.url or "").lower().startswith(("ws://", "wss://")):
            continue
        sampler = _build_http_sampler(entry)
        if sampler is not None:
            ght.append(sampler)
            ET.SubElement(ght, "hashTree")

    return ET.ElementTree(root)


def write_xml(tree: ET.ElementTree, path: str | Path) -> None:
    """Write indented XML to file path."""
    target_path = Path(path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        ET.indent(tree, space="  ")
    except Exception:
        pass
    tree.write(str(target_path), encoding="utf-8", xml_declaration=True)
