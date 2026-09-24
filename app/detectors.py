"""Dependency detectors. Port of Services/Dependencies/{Url,Header,QueryParameter,
Cookie,Body}Dependency.java. Shared recursion replaces 5 copies of the walker."""
from __future__ import annotations
import json
from urllib.parse import urlparse
from .graph import DependencyGraph, MyNode, make_edge
from .response_model import AtomicObject, StructuredObject, ResponseUnstructured
from . import validator as V
from . import schema_client

BLACKLIST_HEADERS = {"sec-ch-ua", "sec-ch-ua-platform", "Accept-Language", "User-Agent",
                     "sec-ch-ua-mobile", "baggage", "sentry-trace"}


def _children(resp) -> list:
    if isinstance(resp, ResponseUnstructured):
        return resp.objects
    if isinstance(resp, StructuredObject):
        return resp.objects
    return []


def _walk_atomic(resp, fn) -> None:
    """DFS over response tree calling fn(atomic, father) for each AtomicObject."""
    def rec(node, father):
        for o in _children(node):
            if isinstance(o, AtomicObject):
                fn(o, father if isinstance(father, StructuredObject) else None)
            elif isinstance(o, StructuredObject):
                rec(o, o)
    rec(resp, resp if isinstance(resp, StructuredObject) else None)


def check_url(responses: list, req_idx: int, graph: DependencyGraph,
              to: MyNode, first: int) -> None:
    segs = [s for s in urlparse(to.request.url).path.split("/") if s != ""]
    # Java splits on "/" keeping leading empty; iterate i>=1 with possible_name=segs[i-1]
    raw = urlparse(to.request.url).path.split("/")
    for i in range(1, len(raw)):
        sub, possible = raw[i], raw[i - 1]
        if not sub:
            continue
        for ri in range(first, req_idx):
            frm = graph.get_node_by_index(ri)
            if frm is None:
                continue
            def fn(atomic: AtomicObject, father, _sub=sub, _poss=possible,
                   _frm=frm, _ri=ri):
                if _sub == atomic.value and V.evaluate_url_atomic_dep(
                        atomic, _frm.request.url, father, _poss):
                    graph.edges.append(make_edge("url", _frm, to, atomic, _ri, req_idx,
                                                 subPath=_sub))
            _walk_atomic(responses[ri], fn)


def check_header(responses: list, req_idx: int, graph: DependencyGraph,
                 to: MyNode, first: int) -> None:
    for h in to.request.headers:
        if h.name in BLACKLIST_HEADERS:
            continue
        for ri in range(first, req_idx):
            frm = graph.get_node_by_index(ri)
            if frm is None:
                continue
            def fn(atomic: AtomicObject, father, _h=h, _frm=frm, _ri=ri):
                if V.evaluate_header_atomic_dep(atomic, _h.name, _h.value, father,
                                                _frm.request.url):
                    graph.edges.append(make_edge("header", _frm, to, atomic, _ri, req_idx,
                                                 header_name=_h.name))
            _walk_atomic(responses[ri], fn)


def check_query(responses: list, req_idx: int, graph: DependencyGraph,
                to: MyNode, first: int) -> None:
    for q in to.request.queryParams:
        for ri in range(first, req_idx):
            frm = graph.get_node_by_index(ri)
            if frm is None:
                continue
            def fn(atomic: AtomicObject, father, _q=q, _frm=frm, _ri=ri):
                if V.general_atomic_comparison(atomic, _q.value, _q.name, father,
                                               _frm.request.url):
                    graph.edges.append(make_edge("queryparam", _frm, to, atomic, _ri,
                                                 req_idx, query_param_name=_q.name))
            _walk_atomic(responses[ri], fn)


def check_cookie(responses: list, req_idx: int, graph: DependencyGraph,
                 to: MyNode, first: int) -> None:
    for c in to.request.cookies:
        for ri in range(first, req_idx):
            frm = graph.get_node_by_index(ri)
            if frm is None:
                continue
            def fn(atomic: AtomicObject, father, _c=c, _frm=frm, _ri=ri):
                if V.evaluate_cookie_atomic_dep(atomic, _c.name, _c.value, father,
                                                _frm.request.url):
                    graph.edges.append(make_edge("cookie", _frm, to, atomic, _ri, req_idx,
                                                 name=_c.name))
            _walk_atomic(responses[ri], fn)


def _schema_match(request_obj_str: str, resp_node) -> StructuredObject | None:
    """Return matching StructuredObject if schema(request)==schema(response node)."""
    want = schema_client.generate_json_schema(request_obj_str)
    stack = [resp_node]
    while stack:
        cur = stack.pop()
        for o in _children(cur):
            if isinstance(o, StructuredObject):
                if o.schema_string == want:
                    return o
                stack.append(o)
    return None


def check_body(responses: list, req_idx: int, graph: DependencyGraph,
               to: MyNode, first: int) -> None:
    pd = to.request.postData
    if pd is None:
        return
    if pd.mimeType == "application/x-www-form-urlencoded":
        for p in pd.params:
            for ri in range(first, req_idx):
                frm = graph.get_node_by_index(ri)
                if frm is None:
                    continue
                def fn(atomic: AtomicObject, father, _p=p, _frm=frm, _ri=ri):
                    if V.general_atomic_comparison(atomic, _p.value, _p.name, father,
                                                   _frm.request.url):
                        graph.edges.append(make_edge("bodyue", _frm, to, atomic, _ri,
                                                     req_idx, name=_p.name))
                _walk_atomic(responses[ri], fn)
    elif pd.mimeType == "application/json":
        text = (pd.text or "").strip()
        if not text:
            return
        try:
            body = json.loads(text)
        except Exception:
            return
        _check_json_value(responses, body, None, req_idx, graph, to, first)


def _check_json_value(responses, obj, name: str | None, req_idx: int, graph: DependencyGraph,
                      to: MyNode, first: int) -> None:
    import json as _j
    if isinstance(obj, dict):
        found = _search_schema(responses, _j.dumps(obj, separators=(",", ":")), req_idx,
                               graph, to, name, first)
        if found:
            return
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                _check_json_value(responses, v, k, req_idx, graph, to, first)
            elif v is not None:
                _check_primitive(responses, str(v), k, req_idx, graph, to, first)
    elif isinstance(obj, list):
        found = _search_schema(responses, _j.dumps(obj, separators=(",", ":")), req_idx,
                               graph, to, name, first)
        if found:
            return
        for el in obj:
            if isinstance(el, (dict, list)):
                _check_json_value(responses, el, name, req_idx, graph, to, first)
            elif el is not None:
                _check_primitive(responses, str(el), name or "", req_idx, graph, to, first)
    elif isinstance(obj, str):
        _check_primitive(responses, obj, name or "", req_idx, graph, to, first)


def _search_schema(responses, obj_str: str, req_idx: int, graph: DependencyGraph,
                   to: MyNode, name: str | None, first: int) -> bool:
    hit = False
    for ri in range(first, req_idx):
        frm = graph.get_node_by_index(ri)
        if frm is None:
            continue
        m = _schema_match(obj_str, responses[ri])
        if m is not None:
            e = make_edge("bodyjson", frm, to, None, ri, req_idx,
                          name=name if name else "All", primitive=False, structured=m)
            if m.name == "All":
                e.name = "All"
            graph.edges.append(e)
            hit = True
    return hit


def _check_primitive(responses, value: str, name: str, req_idx: int,
                     graph: DependencyGraph, to: MyNode, first: int) -> None:
    for ri in range(first, req_idx):
        frm = graph.get_node_by_index(ri)
        if frm is None:
            continue
        def fn(atomic: AtomicObject, father, _v=value, _n=name, _frm=frm, _ri=ri):
            if V.general_atomic_comparison(atomic, _v, _n, father, _frm.request.url):
                graph.edges.append(make_edge("bodyjson", _frm, to, atomic, _ri, req_idx,
                                             name=_n, primitive=True))
        _walk_atomic(responses[ri], fn)
