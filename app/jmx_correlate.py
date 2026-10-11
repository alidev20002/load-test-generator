"""Inject correlations into base JMX. Port of JMeterAdaption.java (HTTP only, no WSS)."""
from __future__ import annotations
import json
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from .graph import DependencyGraph, MyNode, Edge, MANUALLY_INSERTED, MANUALLY_CSV
from .jmx_base import should_skip

NOT_FOUND = "NOT_FOUND"


# ---------- JSON helpers (port of findJsonPathRecursive + replace*JSONObject) ----------

def find_json_path(obj, key: str, cur: str = "$"):
    """DFS first-match path like $['a'][0]['b']. None if absent."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{cur}['{k}']"
            if k == key:
                return p
            r = find_json_path(v, key, p)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            r = find_json_path(v, key, f"{cur}[{i}]")
            if r is not None:
                return r
    return None


def bracket_to_dot(path: str) -> str:
    import re
    out = "$"
    for m in re.finditer(r"\['([^]]+)'\]|\[(\d+)\]", path):
        if m.group(1) is not None:
            out += "." + m.group(1)
        else:
            out += f"[{m.group(2)}]"
    return out


def _parse_bracket(path: str):
    import re
    toks = []
    for m in re.finditer(r"\['([^]]+)'\]|\[(\d+)\]", path):
        toks.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
    return toks


def set_by_bracket_path(data_str: str, bracket_path: str, value: str) -> str:
    data = json.loads(data_str)
    node = data
    toks = _parse_bracket(bracket_path)
    for t in toks[:-1]:
        node = node[t]
    node[toks[-1]] = value
    return json.dumps(data, separators=(",", ":"))


def _unquote_field(js: str, key: str, var_expr: str) -> str:
    """Port of replaceFieldValue: \"${k}\":\"${...}\" -> \"${k}\":${...} for objects."""
    return js.replace(f'"{key}":"{var_expr}"', f'"{key}":{var_expr}')


# ---------- sampler navigation ----------

def _children_by_tag(elem: ET.Element, tag: str):
    return [c for c in elem if c.tag == tag]


def _str_prop(elem: ET.Element, name: str):
    for c in elem:
        if c.tag == "stringProp" and c.get("name") == name:
            return c
    return None


def _thread_group_hashtree(root: ET.Element) -> ET.Element | None:
    for tg in root.iter("ThreadGroup"):
        # parent hashTree: find in tree by walking
        pass
    # structure: jmeterTestPlan/hashTree/hashTree[i] ; locate ThreadGroup's following hashTree
    for ht in root.iter("hashTree"):
        kids = list(ht)
        for i, k in enumerate(kids):
            if k.tag == "ThreadGroup" and i + 1 < len(kids) and kids[i + 1].tag == "hashTree":
                return kids[i + 1]
    return None


def _samplers_in_order(ght: ET.Element):
    kids = list(ght)
    out = []
    for i, k in enumerate(kids):
        if k.tag == "HTTPSamplerProxy":
            ht = kids[i + 1] if i + 1 < len(kids) and kids[i + 1].tag == "hashTree" else None
            out.append((k, ht))
    return out


# ---------- replacements ----------

def _ref(dep_name: str, from_idx: int, dep_value: str) -> str:
    if dep_value in (MANUALLY_INSERTED, MANUALLY_CSV):
        return dep_name  # literal (manual or CSV column value handled by CSV config/UDV)
    return "${" + f"{dep_name}_{from_idx}" + "}"


def replace_url(sampler: ET.Element, deps: list[Edge], node: MyNode) -> None:
    path_el = _str_prop(sampler, "HTTPSampler.path")
    # Java reads full URL from node and rewrites path element with full new URL
    try:
        uri = urlparse(node.request.url)
    except Exception:
        return
    segs = uri.path.split("/")
    changed = False
    for e in deps:
        if e.type != "url" or not e.dependency:
            continue
        for i, s in enumerate(segs):
            if s == e.subPath:
                segs[i] = _ref(e.dependency.name, e.from_index, e.dependency.value)
                changed = True
                break
    if not changed or path_el is None:
        return
    new_path = "/" + "/".join(s for s in segs if s != "") if any(s != "" for s in segs) else "/"
    auth = uri.hostname or ""
    if uri.port:
        auth += f":{uri.port}"
    new_url = f"{uri.scheme}://{auth}{new_path}"
    if uri.query:
        new_url += f"?{uri.query}"
    if uri.fragment:
        new_url += f"#{uri.fragment}"
    path_el.text = new_url


def replace_headers(sampler: ET.Element, deps: list[Edge]) -> None:
    for ep in sampler.iter("elementProp"):
        if ep.get("name") != "HTTPSampler.header_manager":
            continue
        for h in ep.iter("elementProp"):
            hname = h.get("name", "")
            # Cookies are managed by HTTP Cookie Manager (see replace_cookies),
            # never as manual headers.
            if (hname or "").lower() in ("cookie", "set-cookie"):
                continue
            for e in deps:
                if e.type != "header" or not e.dependency or hname != e.header_name:
                    continue
                val = _ref(e.dependency.name, e.from_index, e.dependency.value)
                if hname == "Authorization" and e.dependency.value not in (
                        MANUALLY_INSERTED, MANUALLY_CSV):
                    val = "Bearer " + val
                for sp in h:
                    if sp.tag == "stringProp" and sp.get("name") == "Header.value":
                        sp.text = val


def _ensure_header_manager(sampler: ET.Element):
    """Return (manager_prop, collection) creating them if missing."""
    for ep in sampler:
        if ep.tag == "elementProp" and ep.get("name") == "HTTPSampler.header_manager":
            for coll in ep:
                if coll.tag == "collectionProp":
                    return ep, coll
    mgr = ET.Element("elementProp", {"name": "HTTPSampler.header_manager",
                                     "elementType": "HeaderManager"})
    coll = ET.SubElement(mgr, "collectionProp", {"name": "HeaderManager.headers"})
    sampler.append(mgr)
    return mgr, coll


def replace_cookies(sampler: ET.Element, deps: list[Edge]) -> None:
    """Manual cookie overrides only.

    Automatic cookie dependencies (Set-Cookie -> Cookie) need no JMX change:
    the top-level HTTP Cookie Manager (added in jmx_load.apply_workload)
    captures Set-Cookie responses and replays them. Writing recorded values
    back as hardcoded headers would break replay, so base build excludes the
    Cookie header entirely. Only literal/CSV cookie edges need an explicit
    Cookie header here.
    """
    manual = [e for e in deps
              if e.type == "cookie" and e.dependency is not None
              and e.dependency.value in (MANUALLY_INSERTED, MANUALLY_CSV)]
    if not manual:
        return
    pairs = []
    for e in manual:
        ref = _ref(e.dependency.name, e.from_index, e.dependency.value)
        pairs.append(f"{e.name}={ref}")
    cookie_value = "; ".join(pairs)
    # Reuse an existing Cookie header if present (old base docs), else create.
    for ep in sampler.iter("elementProp"):
        if ep.get("name") != "HTTPSampler.header_manager":
            continue
        for h in ep.iter("elementProp"):
            if (h.get("name", "") or "").lower() == "cookie":
                for sp in h:
                    if sp.tag == "stringProp" and sp.get("name") == "Header.value":
                        sp.text = cookie_value
                return
    _, coll = _ensure_header_manager(sampler)
    hp = ET.SubElement(coll, "elementProp", {"name": "Cookie", "elementType": "Header"})
    c = ET.SubElement(hp, "stringProp", {"name": "Header.name"})
    c.text = "Cookie"
    c = ET.SubElement(hp, "stringProp", {"name": "Header.value"})
    c.text = cookie_value


def replace_args(sampler: ET.Element, deps: list[Edge]) -> None:
    """Query params + urlencoded form (Java only handled queryparam; we also handle bodyue)."""
    for ep in sampler:
        if ep.tag != "elementProp" or ep.get("name") != "HTTPsampler.Arguments":
            continue
        for coll in ep:
            if coll.tag != "collectionProp":
                continue
            for arg in coll:
                if arg.tag != "elementProp":
                    continue
                aname = arg.get("name", "")
                for e in deps:
                    match = (e.type == "queryparam" and aname == e.query_param_name) or \
                            (e.type == "bodyue" and aname == e.name)
                    if match and e.dependency:
                        for sp in arg:
                            if sp.tag == "stringProp" and sp.get("name") == "Argument.value":
                                sp.text = _ref(e.dependency.name, e.from_index,
                                               e.dependency.value)
                            if sp.tag == "boolProp" and sp.get("name") == \
                                    "HTTPArgument.always_encode":
                                sp.text = "false"


def _body_value_elements(sampler: ET.Element):
    out = []
    for ep in sampler:
        if ep.tag != "elementProp" or ep.get("name") != "HTTPsampler.Arguments":
            continue
        for coll in ep:
            if coll.tag != "collectionProp":
                continue
            for arg in coll:
                if arg.tag != "elementProp":
                    continue
                for sp in arg:
                    if sp.tag == "stringProp" and sp.get("name") == "Argument.value":
                        out.append(sp)
    return out


def replace_json_body(sampler: ET.Element, sampler_ht: ET.Element | None,
                      deps: list[Edge], node: MyNode) -> None:
    bodies = [e for e in deps if e.type == "bodyjson"]
    if not bodies:
        return
    if node.request.postData is None or "application/json" not in (node.request.postData.mimeType or ""):
        return
    targets = _body_value_elements(sampler)
    if not targets:
        return
    data = targets[0].text or ""
    whole = [e for e in bodies if not e.primitive and e.name == "All"]
    if whole:
        if len(bodies) == 1:
            e = whole[0]
            ref = "${" + f"{e.structured.name}_{e.from_index}" + "}" if e.structured else data
            targets[0].text = ref
        else:
            _add_groovy_preprocessor(sampler_ht, bodies, node)
            targets[0].text = "${modifiedBody_" + str(node.indexs) + "}"
        return
    try:
        template = json.loads(node.request.postData.text or "{}")
    except Exception:
        template = {}
    for e in bodies:
        path = find_json_path(template, e.name, "$")
        if path is None:
            continue
        if e.primitive and e.dependency:
            ref = _ref(e.dependency.name, e.from_index, e.dependency.value)
            try:
                data = set_by_bracket_path(data, path, ref)
            except Exception:
                continue
        elif not e.primitive and e.structured:
            ref = "${" + f"{e.structured.name}_{e.from_index}" + "}"
            try:
                data = set_by_bracket_path(data, path, ref)
                data = _unquote_field(data, e.name, ref)
            except Exception:
                continue
    targets[0].text = data


def _add_groovy_preprocessor(sampler_ht: ET.Element | None, bodies: list[Edge],
                             node: MyNode) -> None:
    if sampler_ht is None:
        return
    prev_all = next((e.from_index for e in bodies
                     if not e.primitive and e.name == "All"), bodies[0].from_index)
    lines = ["import com.jayway.jsonpath.JsonPath",
             "import com.jayway.jsonpath.DocumentContext", "",
             f'def originalJson = vars.get("All_{prev_all}")',
             "DocumentContext jsonDoc = JsonPath.parse(originalJson)",
             "def substitutions = ["]
    try:
        template = json.loads(node.request.postData.text or "{}")
    except Exception:
        template = {}
    for e in bodies:
        if not e.primitive and e.name == "All":
            continue
        arr_path = find_json_path(template, e.name, "$")
        dot = bracket_to_dot(arr_path) if arr_path else "$." + e.name
        if e.primitive and e.dependency:
            if e.dependency.value in (MANUALLY_INSERTED, MANUALLY_CSV):
                lines.append(f"[path:'{dot}', newValue:'{e.dependency.name}'],")
            else:
                lines.append(f"[path:'{dot}', newValue:'${{{e.dependency.name}_{e.from_index}}}'],")
        elif not e.primitive and e.structured:
            lines.append(f"[path:'{dot}', newValue:'${{{e.structured.name}_{e.from_index}}}'],")
    lines += ["]", "substitutions.each { sub ->", "    jsonDoc.set(sub.path, sub.newValue)", "}",
              "String modifiedJson = jsonDoc.jsonString()",
              f'vars.put("modifiedBody_{node.indexs}",modifiedJson)']
    pre = ET.Element("JSR223PreProcessor",
                     {"guiclass": "TestBeanGUI", "testclass": "JSR223PreProcessor",
                      "testname": "Modify previously saved json response", "enabled": "true"})
    for n, t in (("scriptLanguage", "groovy"), ("parameters", ""), ("filename", ""),
                 ("cacheKey", "true")):
        c = ET.SubElement(pre, "stringProp", {"name": n})
        c.text = t
    sc = ET.SubElement(pre, "stringProp", {"name": "script"})
    sc.text = "\n".join(lines)
    sampler_ht.append(pre)


def add_json_extractor(sampler_ht: ET.Element, deps: list) -> None:
    """Port of addRegExtractorMain: one JSONPostProcessor for all extracted vars."""
    names, paths = [], []
    for o in deps:
        from .response_model import AtomicObject, StructuredObject
        if isinstance(o, AtomicObject):
            if o.from_set_cookie:
                continue
            # need originating index: find in edges is done by caller; here xpath only
            names.append(getattr(o, "_ref_name", o.name))
            paths.append(o.xpath or "")
        elif isinstance(o, StructuredObject):
            names.append(getattr(o, "_ref_name", o.name))
            paths.append(o.xpath or "")
    if not names:
        return
    ext = ET.Element("JSONPostProcessor",
                     {"guiclass": "JSONPostProcessorGui", "testclass": "JSONPostProcessor",
                      "testname": "JSON Extractor", "enable": "true"})
    for n, t in (("JSONPostProcessor.referenceNames", ";".join(names)),
                 ("JSONPostProcessor.jsonPathExprs", ";".join(paths)),
                 ("JSONPostProcessor.match_numbers", ""),
                 ("JSONPostProcessor.defaultValues",
                  ";".join(["NOT_FOUND"] * len(names)))):
        c = ET.SubElement(ext, "stringProp", {"name": n})
        c.text = t
    sampler_ht.append(ext)
    ET.SubElement(sampler_ht, "hashTree")


def _csv_element(csv) -> ET.Element:
    el = ET.Element("CSVDataSet", {"guiclass": "TestBeanGUI", "testclass": "CSVDataSet",
                                   "testname": f"CSV Data Set Config {csv.filename}",
                                   "enabled": "true"})
    for n, t in (("filename", csv.filename), ("fileEncoding", csv.encoding),
                 ("variableNames", csv.variablesName),
                 ("ignoreFirstLine", str(csv.ignorefirstLine).lower()),
                 ("delimiter", ","), ("quotedData", "false"), ("recycle", "true"),
                 ("stopThread", "false"), ("shareMode", "shareMode.all")):
        c = ET.SubElement(el, "stringProp" if "Prop" not in n else "stringProp", {"name": n})
        if n in ("ignoreFirstLine", "quotedData", "recycle", "stopThread"):
            c.tag = "boolProp"
        c.text = t
    return el


# ---------- entry point ----------

def correlate(tree: ET.ElementTree, graph: DependencyGraph, entries) -> ET.ElementTree:
    root = tree.getroot()
    ght = _thread_group_hashtree(root)
    if ght is None:
        raise RuntimeError("ThreadGroup hashTree not found in base JMX")
    # CSV configs first (port of appendCSVConfig)
    for csv in graph.csv_deps():
        ght.insert(0, ET.Element("hashTree"))
        ght.insert(0, _csv_element(csv))
    # align samplers with HAR entry indexes (same filter as base build)
    filt = [(i, e) for i, e in enumerate(entries)
            if not should_skip(e.request.url)
            and not (e.request.url or "").lower().startswith(("ws://", "wss://"))]
    samplers = _samplers_in_order(ght)
    if len(samplers) != len(filt):
        raise RuntimeError(f"sampler/entry mismatch: {len(samplers)} vs {len(filt)}")
    for (har_idx, entry), (sampler, sht) in zip(filt, samplers):
        node = graph.get_node_by_index(har_idx)
        if node is None:
            # fallback: match by URL+method (robust when graph was edited)
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
        # tag ref names with originating index for extractor element
        for o in extracted:
            o._ref_name = f"{o.name}_{har_idx}"  # type: ignore[attr-defined]
        if extracted and sht is not None:
            add_json_extractor(sht, extracted)
    return tree
