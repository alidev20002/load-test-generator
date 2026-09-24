"""Dependency graph model. Port of Entity/{DependencyGraph,MyNode,Edge*,CSVNode}.java.

JSON format: {"nodes": [...], "edges": [...]} with a "type" discriminator,
compatible in spirit with the Java TypeEdgeAdaptor/TypeFromNodeAdaptor.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from .har_model import Request
from .response_model import AtomicObject, StructuredObject

MANUALLY_INSERTED = "manually_inserted"
MANUALLY_CSV = "manually_csv"


@dataclass
class MyNode:
    request: Request
    indexs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return {"kind": "node", "request": asdict(self.request), "indexs": list(self.indexs)}

    @staticmethod
    def from_dict(d: dict) -> "MyNode":
        from .har_model import Header, QueryParam, Cookie, PostData
        r = d.get("request", {})
        req = Request(
            method=r.get("method", ""), url=r.get("url", ""),
            headers=[Header(**h) if isinstance(h, dict) else h for h in r.get("headers", []) or []],
            queryParams=[QueryParam(**q) if isinstance(q, dict) else q
                         for q in r.get("queryParams", []) or []],
            cookies=[Cookie(**c) if isinstance(c, dict) else c for c in r.get("cookies", []) or []],
            postData=PostData(**r["postData"]) if isinstance(r.get("postData"), dict) else None,
        )
        # PostData nested params may be dicts already handled by dataclass init
        if req.postData is not None and req.postData.params \
                and isinstance(req.postData.params[0], dict):
            from .har_model import Param
            req.postData.params = [Param(**p) for p in req.postData.params]
        return MyNode(request=req, indexs=list(d.get("indexs", [])))

    def __repr__(self):
        return f"[{self.request.method}]{self.request.url} {self.indexs}"


@dataclass
class CSVNode(MyNode):
    ignorefirstLine: bool = False
    encoding: str = "UTF-8"
    variablesName: str = ""
    filename: str = ""

    def to_dict(self) -> dict:
        d = super().to_dict()
        d.update({"kind": "csv", "ignorefirstLine": self.ignorefirstLine,
                  "encoding": self.encoding, "variablesName": self.variablesName,
                  "filename": self.filename})
        return d

    @staticmethod
    def from_dict(d: dict) -> "CSVNode":  # type: ignore[override]
        base = MyNode.from_dict(d)
        return CSVNode(request=base.request, indexs=base.indexs,
                       ignorefirstLine=bool(d.get("ignorefirstLine", False)),
                       encoding=str(d.get("encoding", "UTF-8")),
                       variablesName=str(d.get("variablesName", "")),
                       filename=str(d.get("filename", "")))


def _node_from_dict(d: dict) -> MyNode:
    if d.get("kind") == "csv" or "ignorefirstLine" in d:
        return CSVNode.from_dict(d)
    return MyNode.from_dict(d)


@dataclass
class Edge:
    from_node: MyNode | CSVNode | None
    to_node: MyNode
    type: str = ""
    dependency: AtomicObject | None = None
    from_index: int = -1
    to_index: int = -1

    # type-specific payloads (only one used per subclass)
    subPath: str = ""
    header_name: str = ""
    query_param_name: str = ""
    name: str = ""  # cookie / bodyue / bodyjson key
    primitive: bool = True
    structured: StructuredObject | None = None

    def var_name(self) -> str:
        if self.type == "bodyjson" and not self.primitive and self.structured:
            return self.structured.name
        return self.dependency.name if self.dependency else ""

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "from": self.from_node.to_dict() if self.from_node else None,
            "to": self.to_node.to_dict(),
            "dependency": self.dependency.to_dict() if self.dependency else None,
            "from_index": self.from_index, "to_index": self.to_index,
            "subPath": self.subPath, "header_name": self.header_name,
            "query_param_name": self.query_param_name, "name": self.name,
            "primitive": self.primitive,
            "structuredObject": self.structured.to_dict() if self.structured else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "Edge":
        f = _node_from_dict(d["from"]) if d.get("from") else None
        t = _node_from_dict(d["to"])
        dep = AtomicObject.from_dict(d["dependency"]) if d.get("dependency") else None
        st = StructuredObject.from_dict(d["structuredObject"]) if d.get("structuredObject") else None
        return Edge(from_node=f, to_node=t, type=d.get("type", ""), dependency=dep,
                    from_index=int(d.get("from_index", -1)), to_index=int(d.get("to_index", -1)),
                    subPath=d.get("subPath", "") or "", header_name=d.get("header_name", "") or "",
                    query_param_name=d.get("query_param_name", "") or "",
                    name=d.get("name", "") or "", primitive=bool(d.get("primitive", True)),
                    structured=st)


def make_edge(edge_type: str, from_node, to_node, dependency: AtomicObject | None,
              from_index: int, to_index: int, **kw) -> Edge:
    return Edge(from_node=from_node, to_node=to_node, type=edge_type, dependency=dependency,
                from_index=from_index, to_index=to_index, **kw)


class DependencyGraph:
    def __init__(self):
        self.nodes: list[MyNode] = []
        self.edges: list[Edge] = []

    # ---- construction (port of DependencyGraph.addRequestoToGraph) ----
    def add_request(self, request: Request, index: int) -> MyNode:
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
        for n in self.nodes:
            if isinstance(n, CSVNode):
                continue
            if idx in n.indexs:
                return n
        return None

    def get_node_by_url_method(self, url: str, method: str) -> MyNode | None:
        for n in self.nodes:
            if isinstance(n, CSVNode):
                continue
            if n.request.url == url and n.request.method == method:
                return n
        return None

    # ---- queries (port of getDependenciesByNode / getDependenciesToExtract...) ----
    def deps_by_node(self, to: MyNode) -> list[Edge]:
        return [e for e in self.edges
                if e.to_node.request.equals(to.request) and e.to_node.indexs == to.indexs]

    def deps_to_extract(self, frm: MyNode, index: int) -> list:
        out, seen = [], set()
        for e in self.edges:
            if e.from_node is not None and e.from_node.request.equals(frm.request) \
                    and e.from_node.indexs == frm.indexs and e.from_index == index:
                if e.type == "bodyjson" and not e.primitive:
                    key = ("s", e.structured.name if e.structured else e.name)
                    if key not in seen:
                        seen.add(key)
                        out.append(e.structured)
                elif e.dependency is not None and not e.dependency.from_set_cookie:
                    key = ("a", e.dependency.name, e.dependency.value, e.dependency.xpath)
                    if key not in seen:
                        seen.add(key)
                        out.append(e.dependency)
        return out

    def csv_deps(self) -> list[CSVNode]:
        out = []
        for e in self.edges:
            if isinstance(e.from_node, CSVNode) and e.from_node not in out:
                out.append(e.from_node)
        return out

    # ---- persistence ----
    def to_dict(self) -> dict:
        return {"nodes": [n.to_dict() for n in self.nodes],
                "edges": [e.to_dict() for e in self.edges]}

    @staticmethod
    def from_dict(d: dict) -> "DependencyGraph":
        g = DependencyGraph()
        g.nodes = [_node_from_dict(n) for n in d.get("nodes", [])]
        # re-link edges to canonical node objects (match by url+method+indexs)
        def canon(n: MyNode) -> MyNode:
            if isinstance(n, CSVNode):
                for c in g.nodes:
                    if isinstance(c, CSVNode) and c.filename == n.filename \
                            and c.variablesName == n.variablesName:
                        return c
                g.nodes.append(n)
                return n
            for c in g.nodes:
                if not isinstance(c, CSVNode) and c.request.equals(n.request) \
                        and c.indexs == n.indexs:
                    return c
            return n
        for ed in d.get("edges", []):
            e = Edge.from_dict(ed)
            if e.from_node is not None:
                e.from_node = canon(e.from_node)
            e.to_node = canon(e.to_node)
            g.edges.append(e)
        return g

    def save(self, path: str) -> None:
        import json, os
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    @staticmethod
    def load(path: str) -> "DependencyGraph":
        import json
        with open(path, "r", encoding="utf-8") as f:
            return DependencyGraph.from_dict(json.load(f))

    def summary(self) -> str:
        lines = [f"NODES[{len(self.nodes)}]"]
        lines += [f"  {i}: {n}" for i, n in enumerate(self.nodes)]
        lines.append(f"EDGES[{len(self.edges)}]")
        for e in self.edges:
            dep = e.dependency.name if e.dependency else (e.structured.name if e.structured else "?")
            lines.append(f"  [{e.type}] {dep} (from_idx={e.from_index} -> to_idx={e.to_index})")
        return "\n".join(lines)
