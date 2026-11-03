"""Dependency graph model representing request nodes, edges, and correlation mappings."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .har_model import Cookie, Header, PostData, QueryParam, Request
from .response_model import AtomicObject, StructuredObject

MANUALLY_INSERTED = "manually_inserted"
MANUALLY_CSV = "manually_csv"


@dataclass
class MyNode:
    """Graph node representing a unique HTTP request and its execution indices."""
    request: Request
    indexs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "node",
            "request": asdict(self.request),
            "indexs": list(self.indexs),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MyNode:
        r = data.get("request", {})
        req = Request(
            method=str(r.get("method", "")),
            url=str(r.get("url", "")),
            headers=[Header(**h) if isinstance(h, dict) else h for h in (r.get("headers") or [])],
            queryParams=[QueryParam(**q) if isinstance(q, dict) else q for q in (r.get("queryParams") or [])],
            cookies=[Cookie(**c) if isinstance(c, dict) else c for c in (r.get("cookies") or [])],
            postData=PostData.from_dict(r.get("postData")),
        )
        return cls(request=req, indexs=list(data.get("indexs") or []))

    def __repr__(self) -> str:
        return f"[{self.request.method}]{self.request.url} {self.indexs}"


@dataclass
class CSVNode(MyNode):
    """Specialized node representing parameter input sourced from an external CSV file."""
    ignorefirstLine: bool = False
    encoding: str = "UTF-8"
    variablesName: str = ""
    filename: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = super().to_dict()
        data.update({
            "kind": "csv",
            "ignorefirstLine": self.ignorefirstLine,
            "encoding": self.encoding,
            "variablesName": self.variablesName,
            "filename": self.filename,
        })
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CSVNode:
        base = MyNode.from_dict(data)
        return cls(
            request=base.request,
            indexs=base.indexs,
            ignorefirstLine=bool(data.get("ignorefirstLine", False)),
            encoding=str(data.get("encoding", "UTF-8")),
            variablesName=str(data.get("variablesName", "")),
            filename=str(data.get("filename", "")),
        )


def _node_from_dict(data: dict[str, Any]) -> MyNode:
    if data.get("kind") == "csv" or "ignorefirstLine" in data:
        return CSVNode.from_dict(data)
    return MyNode.from_dict(data)


@dataclass
class Edge:
    """Dependency edge connecting a producer node to a consumer node."""
    from_node: MyNode | CSVNode | None
    to_node: MyNode
    type: str = ""
    dependency: AtomicObject | None = None
    from_index: int = -1
    to_index: int = -1

    # Correlation target attributes
    subPath: str = ""
    header_name: str = ""
    query_param_name: str = ""
    name: str = ""
    primitive: bool = True
    structured: StructuredObject | None = None

    def var_name(self) -> str:
        """Return the extracted variable name."""
        if self.type == "bodyjson" and not self.primitive and self.structured:
            return self.structured.name
        return self.dependency.name if self.dependency else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "from": self.from_node.to_dict() if self.from_node else None,
            "to": self.to_node.to_dict(),
            "dependency": self.dependency.to_dict() if self.dependency else None,
            "from_index": self.from_index,
            "to_index": self.to_index,
            "subPath": self.subPath,
            "header_name": self.header_name,
            "query_param_name": self.query_param_name,
            "name": self.name,
            "primitive": self.primitive,
            "structuredObject": self.structured.to_dict() if self.structured else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        from_n = _node_from_dict(data["from"]) if data.get("from") else None
        to_n = _node_from_dict(data["to"])
        dep = AtomicObject.from_dict(data["dependency"]) if data.get("dependency") else None
        st = StructuredObject.from_dict(data["structuredObject"]) if data.get("structuredObject") else None
        return cls(
            from_node=from_n,
            to_node=to_n,
            type=str(data.get("type", "")),
            dependency=dep,
            from_index=int(data.get("from_index", -1)),
            to_index=int(data.get("to_index", -1)),
            subPath=str(data.get("subPath", "") or ""),
            header_name=str(data.get("header_name", "") or ""),
            query_param_name=str(data.get("query_param_name", "") or ""),
            name=str(data.get("name", "") or ""),
            primitive=bool(data.get("primitive", True)),
            structured=st,
        )


def make_edge(
    edge_type: str,
    from_node: MyNode | CSVNode | None,
    to_node: MyNode,
    dependency: AtomicObject | None,
    from_index: int,
    to_index: int,
    **kwargs: Any,
) -> Edge:
    """Convenience factory function for creating Edge instances."""
    return Edge(
        from_node=from_node,
        to_node=to_node,
        type=edge_type,
        dependency=dependency,
        from_index=from_index,
        to_index=to_index,
        **kwargs,
    )


class DependencyGraph:
    """Manages the full directed dependency graph of requests and dynamic values."""

    def __init__(self) -> None:
        self.nodes: list[MyNode] = []
        self.edges: list[Edge] = []

    def add_request(self, request: Request, index: int) -> MyNode:
        """Add request to graph or append execution index if request already exists."""
        for n in self.nodes:
            if isinstance(n, CSVNode):
                continue
            if n.request.equals(request):
                n.indexs.append(index)
                return n
        node = MyNode(request=request, indexs=[index])
        self.nodes.append(node)
        return node

    def get_node_by_index(self, idx: int) -> MyNode | None:
        """Find node whose execution indices include idx."""
        for n in self.nodes:
            if isinstance(n, CSVNode):
                continue
            if idx in n.indexs:
                return n
        return None

    def get_node_by_url_method(self, url: str, method: str) -> MyNode | None:
        """Find node matching target URL and HTTP method."""
        for n in self.nodes:
            if isinstance(n, CSVNode):
                continue
            if n.request.url == url and n.request.method == method:
                return n
        return None

    def deps_by_node(self, to: MyNode) -> list[Edge]:
        """Return all incoming dependency edges for target node."""
        return [
            e for e in self.edges
            if e.to_node.request.equals(to.request) and e.to_node.indexs == to.indexs
        ]

    def deps_to_extract(self, frm: MyNode, index: int) -> list[Any]:
        """Return unique producer objects extracted from frm at execution index."""
        out: list[Any] = []
        seen: set[tuple[Any, ...]] = set()

        for e in self.edges:
            if (
                e.from_node is not None
                and e.from_node.request.equals(frm.request)
                and e.from_node.indexs == frm.indexs
                and e.from_index == index
            ):
                if e.type == "bodyjson" and not e.primitive:
                    key = ("structured", e.structured.name if e.structured else e.name)
                    if key not in seen:
                        seen.add(key)
                        out.append(e.structured)
                elif e.dependency is not None:
                    key = ("atomic", e.dependency.name, e.dependency.value, e.dependency.xpath)
                    if key not in seen:
                        seen.add(key)
                        out.append(e.dependency)

        return out

    def csv_deps(self) -> list[CSVNode]:
        """Return all distinct CSV nodes associated with the graph."""
        seen: set[str] = set()
        result: list[CSVNode] = []
        for e in self.edges:
            if isinstance(e.from_node, CSVNode):
                key = f"{e.from_node.filename}:{e.from_node.variablesName}"
                if key not in seen:
                    seen.add(key)
                    result.append(e.from_node)
        return result

    def to_dict(self) -> dict[str, Any]:
        """Serialize graph to dictionary representation."""
        return {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyGraph:
        """Deserialize graph from dictionary representation."""
        g = cls()
        g.nodes = [_node_from_dict(n) for n in data.get("nodes", [])]

        def resolve_canonical(node: MyNode) -> MyNode:
            if isinstance(node, CSVNode):
                for candidate in g.nodes:
                    if (
                        isinstance(candidate, CSVNode)
                        and candidate.filename == node.filename
                        and candidate.variablesName == node.variablesName
                    ):
                        return candidate
                g.nodes.append(node)
                return node
            for candidate in g.nodes:
                if (
                    not isinstance(candidate, CSVNode)
                    and candidate.request.equals(node.request)
                    and candidate.indexs == node.indexs
                ):
                    return candidate
            return node

        for edge_data in data.get("edges", []):
            edge = Edge.from_dict(edge_data)
            if edge.from_node is not None:
                edge.from_node = resolve_canonical(edge.from_node)
            edge.to_node = resolve_canonical(edge.to_node)
            g.edges.append(edge)

        return g

    def save(self, path: str | Path) -> None:
        """Save graph JSON to disk."""
        target_path = Path(path)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @classmethod
    def load(cls, path: str | Path) -> DependencyGraph:
        """Load graph from JSON file on disk."""
        with Path(path).open("r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def summary(self) -> str:
        """Return human-readable summary of nodes and edges."""
        lines = [f"NODES [{len(self.nodes)}]:"]
        lines.extend(f"  {i}: {node}" for i, node in enumerate(self.nodes))
        lines.append(f"EDGES [{len(self.edges)}]:")
        for edge in self.edges:
            dep_name = (
                edge.dependency.name if edge.dependency
                else (edge.structured.name if edge.structured else "?")
            )
            lines.append(f"  [{edge.type}] {dep_name} (from={edge.from_index} -> to={edge.to_index})")
        return "\n".join(lines)
