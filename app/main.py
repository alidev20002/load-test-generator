"""FastAPI service: Automated HAR to correlated, ready-to-run JMeter Test Plans."""

from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

try:
    from pydantic import ConfigDict
    _HAS_PYDANTIC_V2 = True
except ImportError:
    ConfigDict = None  # type: ignore[assignment,misc]
    _HAS_PYDANTIC_V2 = False

from . import config as app_config
from . import graph_ops
from .extractor import build_graph
from .graph import DependencyGraph, Edge
from .har_model import Entry
from .jmx_base import build_base_doc
from .jmx_correlate import correlate
from .jmx_load import Workload, apply_workload

app = FastAPI(
    title="Load Test Generator",
    description="Automated API Dependency Correlation and JMeter Load Test Plan Generator",
    version="2.0.0",
)

STATIC_DIR = Path(__file__).with_name("static")
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def index():
    """Serve the single-page frontend application."""
    index_file = STATIC_DIR / "index.html"
    if index_file.is_file():
        return FileResponse(str(index_file), media_type="text/html")
    return {"message": "Frontend static assets not found. Visit /docs for API documentation."}


# ---------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------

def _parse_entries_from_bytes(data: bytes) -> list[Entry]:
    try:
        har = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid HAR JSON: {exc}") from exc

    try:
        raw_entries = har.get("log", {}).get("entries", [])
        return [Entry.from_dict(e) for e in raw_entries]
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse HAR entries: {exc}") from exc


def _parse_graph_from_dict(data: dict[str, Any]) -> DependencyGraph:
    try:
        return DependencyGraph.from_dict(data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid graph payload: {exc}") from exc


def _format_graph_payload(graph: DependencyGraph) -> dict[str, Any]:
    return {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "graph": graph.to_dict(),
    }


def _format_edge_info(index: int, edge: Edge) -> dict[str, Any]:
    return {
        "id": index,
        "type": edge.type,
        "from_index": edge.from_index,
        "to_index": edge.to_index,
        "dependency": edge.dependency.to_dict() if edge.dependency else None,
        "key": edge.name or edge.header_name or edge.query_param_name or edge.subPath,
        "subPath": edge.subPath,
        "header_name": edge.header_name,
        "query_param_name": edge.query_param_name,
        "name": edge.name,
        "primitive": edge.primitive,
    }


def _extract_file_stem(filename: str | None) -> str:
    path = Path(filename or "upload.har")
    return path.stem or "test"


def _serialize_tree_to_bytes(tree: ET.ElementTree) -> bytes:
    try:
        ET.indent(tree, space="  ")
    except Exception:
        pass
    buffer = io.BytesIO()
    tree.write(buffer, encoding="utf-8", xml_declaration=True)
    return buffer.getvalue()


# ---------------------------------------------------------------------
# Pydantic Request / Response Schemas
# ---------------------------------------------------------------------

class GraphBody(BaseModel):
    graph: dict[str, Any]


class ListEdgesRequest(BaseModel):
    graph: dict[str, Any]
    to_idx: int | None = None
    edge_type: str | None = Field(default=None, alias="type")

    if _HAS_PYDANTIC_V2:
        model_config = ConfigDict(populate_by_name=True)  # type: ignore[misc]
    else:
        class Config:
            allow_population_by_field_name = True


class AddEdgeRequest(BaseModel):
    graph: dict[str, Any]
    to_idx: int = Field(alias="to")
    edge_type: str = Field(alias="type")
    key: str
    from_idx: int | None = Field(default=None, alias="from")
    resp_name: str | None = None
    resp_value: str | None = None
    resp_xpath: str | None = None
    literal: str | None = None
    csv: str | None = None
    from_har_idx: int = 0
    to_har_idx: int = 0

    if _HAS_PYDANTIC_V2:
        model_config = ConfigDict(populate_by_name=True)  # type: ignore[misc]
    else:
        class Config:
            allow_population_by_field_name = True


class DeleteEdgeRequest(BaseModel):
    graph: dict[str, Any]
    edge_id: int = Field(alias="id")

    if _HAS_PYDANTIC_V2:
        model_config = ConfigDict(populate_by_name=True)  # type: ignore[misc]
    else:
        class Config:
            allow_population_by_field_name = True


class EditEdgeRequest(BaseModel):
    graph: dict[str, Any]
    edge_id: int = Field(alias="id")
    key: str | None = None
    value: str | None = None

    if _HAS_PYDANTIC_V2:
        model_config = ConfigDict(populate_by_name=True)  # type: ignore[misc]
    else:
        class Config:
            allow_population_by_field_name = True


class ConfigUpdate(BaseModel):
    llm_provider: str | None = None
    llm_endpoint: str | None = None
    llm_apikey: str | None = None
    model: str | None = None
    timeout: float | None = None
    fallback_to_lcs: bool | None = None
    cache_enabled: bool | None = None


# ---------------------------------------------------------------------
# Service Endpoints
# ---------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/config")
def get_config() -> dict[str, Any]:
    """Fetch current system configuration."""
    return app_config.get_config()


@app.put("/config")
def update_config(body: ConfigUpdate) -> dict[str, Any]:
    """Update system configuration dictionary."""
    update_data = body.dict(exclude_unset=True) if hasattr(body, "dict") else body.model_dump(exclude_unset=True)
    return app_config.save_config(update_data)


@app.post("/graphs/build")
async def graphs_build(
    har: UploadFile = File(...),
    use_llm: bool = Form(False),
    use_embedding: bool = Form(False),
) -> dict[str, Any]:
    """Build a correlation dependency graph from an uploaded HAR file."""
    data = await har.read()
    entries = _parse_entries_from_bytes(data)
    graph, _, _ = build_graph(
        "",
        entries=entries,
        use_llm=use_llm,
        use_embedding=use_embedding,
    )

    mode = "LLM" if use_llm else ("EMBEDDING" if use_embedding else "LCS")
    result = _format_graph_payload(graph)
    result["mode"] = mode
    return result


@app.post("/graphs/describe")
def graphs_describe(body: GraphBody) -> dict[str, Any]:
    """Return text summary of nodes and correlation edges in the graph."""
    graph = _parse_graph_from_dict(body.graph)
    return {
        "description": graph_ops.describe(graph),
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
    }


@app.post("/graphs/edges/list")
def edges_list(body: ListEdgesRequest) -> dict[str, Any]:
    """List edges filtered by node index or edge type."""
    graph = _parse_graph_from_dict(body.graph)
    try:
        pairs = graph_ops.list_edges(graph, to_idx=body.to_idx, edge_type=body.edge_type)
    except IndexError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"edges": [_format_edge_info(i, e) for i, e in pairs]}


@app.post("/graphs/edges/add")
def edges_add(body: AddEdgeRequest) -> dict[str, Any]:
    """Add a manual or correlated edge to the dependency graph."""
    graph = _parse_graph_from_dict(body.graph)
    try:
        edge = graph_ops.add_edge(
            graph,
            to_idx=body.to_idx,
            edge_type=body.edge_type,
            key=body.key,
            from_idx=body.from_idx,
            resp_name=body.resp_name,
            resp_value=body.resp_value,
            resp_xpath=body.resp_xpath,
            literal=body.literal,
            csv=body.csv,
            from_har_idx=body.from_har_idx,
            to_har_idx=body.to_har_idx,
        )
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = _format_graph_payload(graph)
    payload["edge"] = _format_edge_info(len(graph.edges) - 1, edge)
    return payload


@app.post("/graphs/edges/delete")
def edges_delete(body: DeleteEdgeRequest) -> dict[str, Any]:
    """Delete an edge from the dependency graph."""
    graph = _parse_graph_from_dict(body.graph)
    try:
        removed = graph_ops.delete_edge(graph, body.edge_id)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=f"Edge {body.edge_id} not found") from exc

    payload = _format_graph_payload(graph)
    payload["deleted"] = _format_edge_info(body.edge_id, removed)
    return payload


@app.post("/graphs/edges/edit")
def edges_edit(body: EditEdgeRequest) -> dict[str, Any]:
    """Edit parameters of an existing edge in the graph."""
    graph = _parse_graph_from_dict(body.graph)
    try:
        edge = graph_ops.edit_edge(graph, body.edge_id, key=body.key, value=body.value)
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=f"Edge {body.edge_id} not found") from exc

    payload = _format_graph_payload(graph)
    payload["edge"] = _format_edge_info(body.edge_id, edge)
    return payload


@app.post("/jmx/build")
async def jmx_build(
    har: UploadFile = File(...),
    graph_file: UploadFile | None = File(default=None, alias="graph"),
    auto_graph: bool = Form(False),
    use_llm: bool = Form(False),
    use_embedding: bool = Form(False),
    workload: list[str] = Form(default=[]),
) -> Response:
    """Generate correlated JMeter .jmx test plan from HAR file and dependency graph."""
    har_data = await har.read()
    entries = _parse_entries_from_bytes(har_data)

    try:
        workloads = [Workload.parse(w) for w in workload] if workload else [Workload()]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Bad workload specification: {exc}") from exc

    graph: DependencyGraph | None = None
    if graph_file is not None:
        raw_graph = await graph_file.read()
        try:
            graph = _parse_graph_from_dict(json.loads(raw_graph.decode("utf-8")))
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid graph file: {exc}") from exc
    elif auto_graph:
        graph, _, _ = build_graph(
            "",
            entries=entries,
            use_llm=use_llm,
            use_embedding=use_embedding,
        )
    else:
        raise HTTPException(status_code=422, detail="A graph file or auto_graph=true is required")

    tree = build_base_doc(entries)
    try:
        tree = correlate(tree, graph, entries)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    tree = apply_workload(tree, workloads, tg_name=_extract_file_stem(har.filename))
    xml_data = _serialize_tree_to_bytes(tree)
    filename = f"{_extract_file_stem(har.filename)}.jmx"

    return Response(
        content=xml_data,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
