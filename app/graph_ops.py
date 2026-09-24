"""Graph edit utilities. Terminal replacement for the CustomizeCorrelation GUI.

Covers add / edit / delete / toggle of dependencies; manual + CSV edges use the
same sentinels as Java ("manually_inserted" / "manually_csv").
"""
from __future__ import annotations
from .graph import DependencyGraph, Edge, CSVNode, MyNode, make_edge, MANUALLY_INSERTED, MANUALLY_CSV
from .response_model import AtomicObject, StructuredObject

TYPES = ("url", "header", "queryparam", "cookie", "bodyue", "bodyjson")


def _node_label(g: DependencyGraph, n: MyNode) -> str:
    try:
        i = [x for x in g.nodes if not isinstance(x, CSVNode)].index(n)
    except ValueError:
        i = -1
    return f"[{i}][{n.request.method}]{n.request.url} idx={n.indexs}"


def list_edges(g: DependencyGraph, to_idx: int | None = None,
               edge_type: str | None = None) -> list[tuple[int, Edge]]:
    out = []
    for i, e in enumerate(g.edges):
        if edge_type and e.type != edge_type:
            continue
        if to_idx is not None:
            nodes = [n for n in g.nodes if not isinstance(n, CSVNode)]
            if not (0 <= to_idx < len(nodes)):
                raise IndexError(f"node {to_idx} out of range")
            want = nodes[to_idx]
            if not (e.to_node.request.equals(want.request)
                    and e.to_node.indexs == want.indexs):
                continue
        out.append((i, e))
    return out


def describe(g: DependencyGraph) -> str:
    lines = []
    for i, n in enumerate(g.nodes):
        if isinstance(n, CSVNode):
            lines.append(f"node {i}: [CSV] {n.filename} vars={n.variablesName}")
        else:
            lines.append(f"node {i}: {_node_label(g, n)}")
    for i, e in enumerate(g.edges):
        frm = "MANUAL" if e.from_node is None else _node_label(g, e.from_node)
        dep = e.dependency.name if e.dependency else (e.structured.name if e.structured else "?")
        lines.append(f"edge {i}: [{e.type}] key={e.name or e.header_name or e.query_param_name or e.subPath} "
                     f"dep={dep} from=({frm},idx={e.from_index}) to_idx={e.to_index}")
    return "\n".join(lines)


def delete_edge(g: DependencyGraph, edge_id: int) -> Edge:
    return g.edges.pop(edge_id)


def toggle_edge(g: DependencyGraph, edge_id: int, enabled: bool) -> None:
    """GUI checkboxes only filtered on save; terminal equivalent: delete when off,
    so keep a trash list on the graph object."""
    trash: list = getattr(g, "_trash", [])
    if not enabled:
        trash.append(g.edges.pop(edge_id))
    g._trash = trash  # type: ignore[attr-defined]


def _resolve_nodes(g: DependencyGraph, to_idx: int, from_idx: int | None) -> tuple[MyNode, MyNode | None]:
    nodes = [n for n in g.nodes if not isinstance(n, CSVNode)]
    to = nodes[to_idx]
    frm = nodes[from_idx] if from_idx is not None else None
    return to, frm


def add_edge(g: DependencyGraph, to_idx: int, edge_type: str, key: str,
             from_idx: int | None = None, resp_name: str | None = None,
             resp_value: str | None = None, resp_xpath: str | None = None,
             literal: str | None = None, csv: str | None = None,
             from_har_idx: int = 0, to_har_idx: int = 0) -> Edge:
    """Add a dependency. Either link a response field (--resp-*) or set a literal/CSV value.

    csv format: filename:vars  (e.g. data/users.csv:username)
    """
    if edge_type not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    to, frm = _resolve_nodes(g, to_idx, from_idx)
    kw: dict = {}
    if edge_type == "url":
        kw["subPath"] = key
    elif edge_type == "header":
        kw["header_name"] = key
    elif edge_type == "queryparam":
        kw["query_param_name"] = key
    else:
        kw["name"] = key
    if edge_type == "bodyjson" and resp_name is None and literal is None and csv is None:
        # whole-response reference is expressed via structured 'All' in detectors;
        # manual whole-body uses literal
        pass
    if literal is not None:
        dep = AtomicObject(value=MANUALLY_INSERTED, name=literal, xpath="")
        e = make_edge(edge_type, None, to, dep, -1, to_har_idx, **kw)
    elif csv is not None:
        filename, _, vars_ = csv.partition(":")
        cnode = CSVNode(request=to.request, indexs=[], ignorefirstLine=True,
                        encoding="UTF-8", variablesName=vars_, filename=filename)
        dep = AtomicObject(value=MANUALLY_CSV, name=literal or vars_.split(",")[0], xpath="")
        e = make_edge(edge_type, cnode, to, dep, -1, to_har_idx, **kw)
        g.nodes.append(cnode)
    else:
        if frm is None or resp_name is None:
            raise ValueError("linking an edge needs --from and --resp-name")
        dep = AtomicObject(value=resp_value or "", name=resp_name, xpath=resp_xpath or "")
        e = make_edge(edge_type, frm, to, dep, from_har_idx, to_har_idx, **kw)
    g.edges.append(e)
    return e


def edit_edge(g: DependencyGraph, edge_id: int, key: str | None = None,
              value: str | None = None) -> Edge:
    e = g.edges[edge_id]
    if key is not None:
        if e.type == "url":
            e.subPath = key
        elif e.type == "header":
            e.header_name = key
        elif e.type == "queryparam":
            e.query_param_name = key
        else:
            e.name = key
    if value is not None and e.dependency is not None:
        e.dependency.name = value
    return e
