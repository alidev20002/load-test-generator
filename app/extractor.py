"""Build the dependency graph from a HAR. Port of ExtractDependencies.java."""
from __future__ import annotations
from .har_model import load_har
from .graph import DependencyGraph
from .response_analyzer import get_unstructured_response, analyze_response_headers
from . import detectors as D
from . import validator as V
from .llm_matcher import LLMConfig


def analyze_responses(entries):
    out = []
    for e in entries:
        mime = e.response.content.mimeType or ""
        if mime == "application/json" and (e.response.content.text or "").strip():
            r = get_unstructured_response(e.response.content.text)
        else:
            from .response_model import ResponseUnstructured
            r = ResponseUnstructured()
        analyze_response_headers(e.response.headers, r)
        out.append(r)
    return out


def build_graph(har_path: str,
                entries=None, responses=None,
                use_llm: bool = False,
                llm_config: LLMConfig | None = None) -> tuple[DependencyGraph, list, list]:
    entries = entries if entries is not None else load_har(har_path)
    responses = responses if responses is not None else analyze_responses(entries)
    g = DependencyGraph()
    if not entries:
        return g, entries, responses
    if use_llm:
        V.enable_llm(llm_config or LLMConfig.from_env())
    try:
        g.add_request(entries[0].request, 0)
        for i in range(1, len(entries)):
            to = g.add_request(entries[i].request, i)
            pos = to.indexs.index(i)
            first = to.indexs[pos - 1] + 1 if pos != 0 else 0
            D.check_url(responses, i, g, to, first)
            D.check_header(responses, i, g, to, first)
            D.check_query(responses, i, g, to, first)
            pd = to.request.postData
            if pd is not None and to.request.method in ("POST", "PUT"):
                D.check_body(responses, i, g, to, first)
            D.check_cookie(responses, i, g, to, first)
    finally:
        if use_llm:
            V.disable_llm()
    return g, entries, responses
