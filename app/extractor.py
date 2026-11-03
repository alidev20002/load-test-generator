"""Build the request dependency graph from recorded HTTP Archive (HAR) entries."""

from __future__ import annotations

from pathlib import Path

from . import detectors as D
from . import validator as V
from .graph import DependencyGraph, MyNode
from .har_model import Entry, load_har
from .response_analyzer import analyze_response_headers, get_unstructured_response
from .response_model import ResponseUnstructured


def analyze_responses(entries: list[Entry]) -> list[ResponseUnstructured]:
    """Parse and flatten response content and headers for each HAR entry."""
    analyzed: list[ResponseUnstructured] = []
    for entry in entries:
        content_type = entry.response.content.mimeType or ""
        body_text = entry.response.content.text or ""

        if "application/json" in content_type.lower() and body_text.strip():
            resp_struct = get_unstructured_response(body_text)
        else:
            resp_struct = ResponseUnstructured()

        analyze_response_headers(entry.response.headers, resp_struct)
        analyzed.append(resp_struct)
    return analyzed


def build_graph(
    har_path: str | Path,
    entries: list[Entry] | None = None,
    responses: list[ResponseUnstructured] | None = None,
    use_llm: bool = False,
    use_embedding: bool = False,
) -> tuple[DependencyGraph, list[Entry], list[ResponseUnstructured]]:
    """Construct dependency graph by evaluating producer-consumer relationships across HAR requests."""
    resolved_entries = entries if entries is not None else load_har(har_path)
    resolved_responses = responses if responses is not None else analyze_responses(resolved_entries)
    graph = DependencyGraph()

    if not resolved_entries:
        return graph, resolved_entries, resolved_responses

    if use_llm:
        V.enable_llm()

    if use_embedding:
        V.enable_embedding()

    try:
        graph.add_request(resolved_entries[0].request, 0)

        for i in range(1, len(resolved_entries)):
            to_node: MyNode = graph.add_request(resolved_entries[i].request, i)
            pos = to_node.indexs.index(i)
            first_idx = to_node.indexs[pos - 1] + 1 if pos != 0 else 0

            D.check_url(resolved_responses, i, graph, to_node, first_idx)
            D.check_header(resolved_responses, i, graph, to_node, first_idx)
            D.check_query(resolved_responses, i, graph, to_node, first_idx)

            post_data = to_node.request.postData
            if post_data is not None and to_node.request.method in ("POST", "PUT"):
                D.check_body(resolved_responses, i, graph, to_node, first_idx)

            D.check_cookie(resolved_responses, i, graph, to_node, first_idx)
    finally:
        if use_llm:
            V.disable_llm()
        if use_embedding:
            V.disable_embedding()

    return graph, resolved_entries, resolved_responses
