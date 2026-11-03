"""FastAPI service: HAR -> dependency graph -> ready-to-run JMX.

Replaces the former ``app.cli`` (argparse). All operations are stateless
HTTP requests; the client holds the graph JSON between calls. LLM and env
settings come from ``app/configs.json`` via :mod:`app.config` — no CLI
flags or per-process env wiring needed.

Run: ``uvicorn app.main:app --reload``
"""
from __future__ import annotations

import io
import json
import os
import xml.etree.ElementTree as ET
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from pydantic import BaseModel, Field

from . import config as app_config
from . import graph_ops
from .extractor import build_graph
from .graph import DependencyGraph
from .har_model import Entry
from .jmx_base import build_base_doc
from .jmx_correlate import correlate
from .jmx_load import Workload, apply_workload

app = FastAPI(title="Load Test Generator", version="2.0.0")

_STATIC = Path(__file__).with_name("static")
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/", include_in_schema=False)
def index():
    idx = _STATIC / "index.html"
    if idx.is_file():
        return FileResponse(str(idx), media_type="text/html")
    return {"message": "UI not built — see /docs"}


# ---------- helpers ----------

def _entries_from_bytes(data: bytes) -> list[Entry]:
    try:
        har = json.loads(data.decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid HAR JSON: {e}")
    try:
        raw = har.get("log", {}).get("entries", [])
        return [Entry.from_dict(e) for e in raw]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"cannot parse HAR entries: {e}")


def _graph_from_dict(d: dict) -> DependencyGraph:
    try:
        return DependencyGraph.from_dict(d)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid graph JSON: {e}")


def _graph_payload(g: DependencyGraph) -> dict:
    return {
        "nodes": len(g.nodes),
        "edges": len(g.edges),
        "graph": g.to_dict(),
    }


def _edge_info(i: int, e) -> dict:
    return {
        "id": i,
        "type": e.type,
        "from_index": e.from_index,
        "to_index": e.to_index,
        "dependency": e.dependency.to_dict() if e.dependency else None,
        "key": e.name or e.header_name or e.query_param_name or e.subPath,
        "subPath": e.subPath,
        "header_name": e.header_name,
        "query_param_name": e.query_param_name,
        "name": e.name,
        "primitive": e.primitive,
    }


def _stem(filename: str) -> str:
    base = os.path.basename(filename or "upload.har")
    return os.path.splitext(base)[0] or "test"


def _resolve_llm(use_llm: bool):
    if not use_llm:
        return None
    return app_config.get_llm_config()


def _tree_to_bytes(tree: ET.ElementTree) -> bytes:
    try:
        ET.indent(tree, space="  ")
    except Exception:
        pass
    buf = io.BytesIO()
    tree.write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


# ---------- pydantic models ----------

class GraphBody(BaseModel):
    graph: dict[str, Any]


class ListEdgesRequest(BaseModel):
    graph: dict[str, Any]
    to_idx: Optional[int] = None
    edge_type: Optional[str] = Field(default=None, alias="type")

    class Config:
        populate_by_name = True


class AddEdgeRequest(BaseModel):
    graph: dict[str, Any]
    to_idx: int = Field(alias="to")
    edge_type: str = Field(alias="type")
    key: str
    from_idx: Optional[int] = Field(default=None, alias="from")
    resp_name: Optional[str] = None
    resp_value: Optional[str] = None
    resp_xpath: Optional[str] = None
    literal: Optional[str] = None
    csv: Optional[str] = None
    from_har_idx: int = 0
    to_har_idx: int = 0

    class Config:
        populate_by_name = True


class DeleteEdgeRequest(BaseModel):
    graph: dict[str, Any]
    edge_id: int = Field(alias="id")

    class Config:
        populate_by_name = True


class EditEdgeRequest(BaseModel):
    graph: dict[str, Any]
    edge_id: int = Field(alias="id")
    key: Optional[str] = None
    value: Optional[str] = None

    class Config:
        populate_by_name = True


class ConfigUpdate(BaseModel):
    env: Optional[dict[str, str]] = None
    llm: Optional[dict[str, Any]] = None


# ---------- meta ----------

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/config")
def get_config() -> dict:
    return app_config._ensure_loaded()


@app.put("/config")
def update_config(body: ConfigUpdate) -> dict:
    current = app_config._ensure_loaded()
    updated = json.loads(json.dumps(current))  # deep copy
    if body.env:
        updated.setdefault("env", {}).update(body.env)
    if body.llm:
        updated.setdefault("llm", {}).update(body.llm)
    return app_config.save(updated)


# ---------- graph ----------

@app.post("/graphs/build")
async def graphs_build(
    har: UploadFile = File(...),
    use_llm: bool = Form(False),
    use_embedding: bool = Form(False),
) -> dict:
    data = await har.read()
    entries = _entries_from_bytes(data)
    g, _, _ = build_graph("", entries=entries, use_llm=use_llm, use_embedding=use_embedding,
                          llm_config=_resolve_llm(use_llm) if use_llm else None)
    if use_llm:
        mode = "LLM"
    elif use_embedding:
        mode = "EMBEDDING"
    else:
        mode = "LCS"
    out = _graph_payload(g)
    out["mode"] = mode
    return out


@app.post("/graphs/describe")
def graphs_describe(body: GraphBody) -> dict:
    g = _graph_from_dict(body.graph)
    return {"description": graph_ops.describe(g),
            "nodes": len(g.nodes), "edges": len(g.edges)}


@app.post("/graphs/edges/list")
def edges_list(body: ListEdgesRequest) -> dict:
    g = _graph_from_dict(body.graph)
    try:
        pairs = graph_ops.list_edges(g, to_idx=body.to_idx, edge_type=body.edge_type)
    except IndexError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"edges": [_edge_info(i, e) for i, e in pairs]}


@app.post("/graphs/edges/add")
def edges_add(body: AddEdgeRequest) -> dict:
    g = _graph_from_dict(body.graph)
    try:
        e = graph_ops.add_edge(
            g, to_idx=body.to_idx, edge_type=body.edge_type, key=body.key,
            from_idx=body.from_idx, resp_name=body.resp_name,
            resp_value=body.resp_value, resp_xpath=body.resp_xpath,
            literal=body.literal, csv=body.csv,
            from_har_idx=body.from_har_idx, to_har_idx=body.to_har_idx)
    except (ValueError, IndexError) as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    out = _graph_payload(g)
    out["edge"] = _edge_info(len(g.edges) - 1, e)
    return out


@app.post("/graphs/edges/delete")
def edges_delete(body: DeleteEdgeRequest) -> dict:
    g = _graph_from_dict(body.graph)
    try:
        removed = graph_ops.delete_edge(g, body.edge_id)
    except IndexError:
        raise HTTPException(status_code=404, detail=f"edge {body.edge_id} not found")
    out = _graph_payload(g)
    out["deleted"] = _edge_info(body.edge_id, removed)
    return out


@app.post("/graphs/edges/edit")
def edges_edit(body: EditEdgeRequest) -> dict:
    g = _graph_from_dict(body.graph)
    try:
        e = graph_ops.edit_edge(g, body.edge_id, key=body.key, value=body.value)
    except IndexError:
        raise HTTPException(status_code=404, detail=f"edge {body.edge_id} not found")
    out = _graph_payload(g)
    out["edge"] = _edge_info(body.edge_id, e)
    return out


# ---------- jmx ----------

@app.post("/jmx/build")
async def jmx_build(
    har: UploadFile = File(...),
    graph_file: Optional[UploadFile] = File(default=None, alias="graph"),
    auto_graph: bool = Form(False),
    use_llm: bool = Form(False),
    use_embedding: bool = Form(False),
    workload: list[str] = Form(default=[]),
) -> Response:
    har_data = await har.read()
    entries = _entries_from_bytes(har_data)
    try:
        workloads = [Workload.parse(w) for w in workload] if workload else [Workload()]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"bad workload: {e}")

    g: DependencyGraph | None = None
    if graph_file is not None:
        raw = await graph_file.read()
        try:
            g = _graph_from_dict(json.loads(raw.decode("utf-8")))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"invalid graph file: {e}")
    elif auto_graph:
        g, _, _ = build_graph("", entries=entries, use_llm=use_llm, use_embedding=use_embedding,
                              llm_config=_resolve_llm(use_llm) if use_llm else None)
    else:
        raise HTTPException(status_code=422,
                            detail="graph file or auto_graph=true is required")

    tree = build_base_doc(entries)
    try:
        tree = correlate(tree, g, entries)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    tree = apply_workload(tree, workloads, tg_name=_stem(har.filename))
    xml = _tree_to_bytes(tree)
    name = f"{_stem(har.filename)}.jmx"
    return Response(content=xml, media_type="application/xml",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
