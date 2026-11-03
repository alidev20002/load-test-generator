"""Graph editing and inspection utilities for managing dependencies and manual correlations."""

from __future__ import annotations

from typing import Any

from .graph import (
    CSVNode,
    DependencyGraph,
    Edge,
    MANUALLY_CSV,
    MANUALLY_INSERTED,
    MyNode,
    make_edge,
)
from .response_model import AtomicObject

SUPPORTED_EDGE_TYPES = ("url", "header", "queryparam", "cookie", "bodyue", "bodyjson")


def _format_node_label(graph: DependencyGraph, node: MyNode) -> str:
    """Generate a clean identifier label for a node within the graph."""
    try:
        http_nodes = [n for n in graph.nodes if not isinstance(n, CSVNode)]
        idx = http_nodes.index(node)
    except ValueError:
        idx = -1
    return f"[{idx}][{node.request.method}]{node.request.url} indices={node.indexs}"


def list_edges(
    graph: DependencyGraph,
    to_idx: int | None = None,
    edge_type: str | None = None,
) -> list[tuple[int, Edge]]:
    """List edges matching optional target node index and edge type criteria."""
    result: list[tuple[int, Edge]] = []
    http_nodes = [n for n in graph.nodes if not isinstance(n, CSVNode)]

    for idx, edge in enumerate(graph.edges):
        if edge_type and edge.type != edge_type:
            continue
        if to_idx is not None:
            if not (0 <= to_idx < len(http_nodes)):
                raise IndexError(f"Node index {to_idx} out of range (max {len(http_nodes) - 1})")
            target = http_nodes[to_idx]
            if not (edge.to_node.request.equals(target.request) and edge.to_node.indexs == target.indexs):
                continue
        result.append((idx, edge))
    return result


def describe(graph: DependencyGraph) -> str:
    """Return a detailed text representation of all nodes and dependency edges."""
    lines: list[str] = []
    for i, node in enumerate(graph.nodes):
        if isinstance(node, CSVNode):
            lines.append(f"Node {i}: [CSV] {node.filename} vars={node.variablesName}")
        else:
            lines.append(f"Node {i}: {_format_node_label(graph, node)}")

    for i, edge in enumerate(graph.edges):
        source = "MANUAL" if edge.from_node is None else _format_node_label(graph, edge.from_node)
        dep_name = (
            edge.dependency.name if edge.dependency
            else (edge.structured.name if edge.structured else "?")
        )
        target_key = edge.name or edge.header_name or edge.query_param_name or edge.subPath
        lines.append(
            f"Edge {i}: [{edge.type}] key={target_key} dep={dep_name} "
            f"from=({source}, idx={edge.from_index}) to_idx={edge.to_index}"
        )
    return "\n".join(lines)


def delete_edge(graph: DependencyGraph, edge_id: int) -> Edge:
    """Remove and return edge by its index in the graph."""
    return graph.edges.pop(edge_id)


def toggle_edge(graph: DependencyGraph, edge_id: int, enabled: bool) -> None:
    """Enable or disable an edge, moving disabled edges to a temporary trash collection."""
    trash: list[Edge] = getattr(graph, "_trash", [])
    if not enabled:
        trash.append(graph.edges.pop(edge_id))
    graph._trash = trash  # type: ignore[attr-defined]


def _resolve_nodes(
    graph: DependencyGraph,
    to_idx: int,
    from_idx: int | None,
) -> tuple[MyNode, MyNode | None]:
    http_nodes = [n for n in graph.nodes if not isinstance(n, CSVNode)]
    if not (0 <= to_idx < len(http_nodes)):
        raise IndexError(f"Target node index {to_idx} out of bounds")
    to_node = http_nodes[to_idx]

    from_node = None
    if from_idx is not None:
        if not (0 <= from_idx < len(http_nodes)):
            raise IndexError(f"Source node index {from_idx} out of bounds")
        from_node = http_nodes[from_idx]

    return to_node, from_node


def add_edge(
    graph: DependencyGraph,
    to_idx: int,
    edge_type: str,
    key: str,
    from_idx: int | None = None,
    resp_name: str | None = None,
    resp_value: str | None = None,
    resp_xpath: str | None = None,
    literal: str | None = None,
    csv: str | None = None,
    from_har_idx: int = 0,
    to_har_idx: int = 0,
) -> Edge:
    """Add a dependency edge to the graph (correlated from response, literal value, or CSV)."""
    if edge_type not in SUPPORTED_EDGE_TYPES:
        raise ValueError(f"Invalid edge type '{edge_type}'. Supported: {SUPPORTED_EDGE_TYPES}")

    to_node, from_node = _resolve_nodes(graph, to_idx, from_idx)
    edge_kwargs: dict[str, Any] = {}

    if edge_type == "url":
        edge_kwargs["subPath"] = key
    elif edge_type == "header":
        edge_kwargs["header_name"] = key
    elif edge_type == "queryparam":
        edge_kwargs["query_param_name"] = key
    else:
        edge_kwargs["name"] = key

    if literal is not None:
        dep = AtomicObject(value=MANUALLY_INSERTED, name=literal, xpath="")
        edge = make_edge(edge_type, None, to_node, dep, -1, to_har_idx, **edge_kwargs)
    elif csv is not None:
        filename, _, vars_spec = csv.partition(":")
        csv_node = CSVNode(
            request=to_node.request,
            indexs=[],
            ignorefirstLine=True,
            encoding="UTF-8",
            variablesName=vars_spec,
            filename=filename,
        )
        dep = AtomicObject(
            value=MANUALLY_CSV,
            name=literal or vars_spec.split(",")[0],
            xpath="",
        )
        edge = make_edge(edge_type, csv_node, to_node, dep, -1, to_har_idx, **edge_kwargs)
        graph.nodes.append(csv_node)
    else:
        if from_node is None or resp_name is None:
            raise ValueError("Linking response dependency requires 'from_idx' and 'resp_name'")
        dep = AtomicObject(
            value=resp_value or "",
            name=resp_name,
            xpath=resp_xpath or "",
        )
        edge = make_edge(edge_type, from_node, to_node, dep, from_har_idx, to_har_idx, **edge_kwargs)

    graph.edges.append(edge)
    return edge


def edit_edge(
    graph: DependencyGraph,
    edge_id: int,
    key: str | None = None,
    value: str | None = None,
) -> Edge:
    """Modify parameters of an existing dependency edge."""
    edge = graph.edges[edge_id]
    if key is not None:
        if edge.type == "url":
            edge.subPath = key
        elif edge.type == "header":
            edge.header_name = key
        elif edge.type == "queryparam":
            edge.query_param_name = key
        else:
            edge.name = key

    if value is not None and edge.dependency is not None:
        edge.dependency.name = value

    return edge
