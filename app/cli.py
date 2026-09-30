"""Terminal CLI: HAR -> dependency graph -> ready-to-run JMX (UltimateThreadGroup)."""
from __future__ import annotations
import argparse
import os
import sys

from .extractor import build_graph
from .graph import DependencyGraph
from . import graph_ops
from .har_model import load_har
from .jmx_base import build_base_doc, write_xml
from .jmx_correlate import correlate
from .jmx_load import Workload, apply_workload
from .llm_matcher import LLMConfig


def _har_stem(har: str) -> str:
    return os.path.splitext(os.path.basename(har))[0]


def _llm_config_from_args(a: argparse.Namespace) -> LLMConfig:
    return LLMConfig(
        provider=getattr(a, "llm_provider", "ollama"),
        endpoint=getattr(a, "llm_endpoint", None) or os.getenv(
            "LLM_ENDPOINT", os.getenv("OLLAMA_HOST", "http://localhost:11434")),
        model=getattr(a, "llm_model", None) or os.getenv("LLM_MODEL", "qwen2.5:0.5b"),
        timeout=float(getattr(a, "llm_timeout", 30.0) or 30.0),
        fallback_to_lcs=not getattr(a, "no_llm_fallback", False),
    )


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--llm-match", action="store_true",
                   help="use lightweight-LLM field-name matching instead of LCS+stemming")
    p.add_argument("--llm-provider", default="ollama",
                   choices=["ollama", "openai-compatible", "stub"],
                   help="LLM backend (default: ollama)")
    p.add_argument("--llm-endpoint", default=None,
                   help="LLM server URL (default: $LLM_ENDPOINT or http://localhost:11434)")
    p.add_argument("--llm-model", default=None,
                   help="model name (default: $LLM_MODEL or qwen2.5:0.5b)")
    p.add_argument("--llm-timeout", type=float, default=30.0)
    p.add_argument("--no-llm-fallback", action="store_true",
                   help="do not fall back to LCS when the LLM is unreachable/ambiguous")


def cmd_build_graph(a: argparse.Namespace) -> int:
    out = a.out or os.path.join("graphs", _har_stem(a.har) + ".graph.json")
    g, _, _ = build_graph(a.har, use_llm=a.llm_match,
                          llm_config=_llm_config_from_args(a) if a.llm_match else None)
    g.save(out)
    mode = "llm" if a.llm_match else "lcs"
    print(f"graph: {len(g.nodes)} nodes, {len(g.edges)} edges [{mode}] -> {out}")
    return 0


def cmd_show(a: argparse.Namespace) -> int:
    print(graph_ops.describe(DependencyGraph.load(a.graph)))
    return 0


def cmd_list(a: argparse.Namespace) -> int:
    g = DependencyGraph.load(a.graph)
    for i, e in graph_ops.list_edges(g, to_idx=a.to, edge_type=a.type):
        dep = e.dependency.name if e.dependency else "?"
        print(f"{i}: [{e.type}] dep={dep} from_idx={e.from_index} to_idx={e.to_index}")
    return 0


def cmd_add(a: argparse.Namespace) -> int:
    g = DependencyGraph.load(a.graph)
    e = graph_ops.add_edge(g, to_idx=a.to, edge_type=a.type, key=a.key, from_idx=a.frm,
                           resp_name=a.resp_name, resp_value=a.resp_value,
                           resp_xpath=a.resp_xpath, literal=a.literal, csv=a.csv,
                           from_har_idx=a.from_har_idx or 0, to_har_idx=a.to_har_idx or 0)
    g.save(a.graph)
    print(f"added [{e.type}] -> {a.graph} (edges={len(g.edges)})")
    return 0


def cmd_del(a: argparse.Namespace) -> int:
    g = DependencyGraph.load(a.graph)
    graph_ops.delete_edge(g, a.id)
    g.save(a.graph)
    print(f"deleted edge {a.id} (edges={len(g.edges)})")
    return 0


def cmd_edit(a: argparse.Namespace) -> int:
    g = DependencyGraph.load(a.graph)
    graph_ops.edit_edge(g, a.id, key=a.key, value=a.value)
    g.save(a.graph)
    print(f"edited edge {a.id}")
    return 0


def cmd_build_jmx(a: argparse.Namespace) -> int:
    workloads = [Workload.parse(w) for w in a.workload] if a.workload else [Workload()]
    graph_path = a.graph
    if not graph_path and a.auto_graph:
        graph_path = os.path.join("graphs", _har_stem(a.har) + ".graph.json")
        g, _, _ = build_graph(a.har, use_llm=a.llm_match,
                              llm_config=_llm_config_from_args(a) if a.llm_match else None)
        g.save(graph_path)
        print(f"auto graph -> {graph_path}")
    if not graph_path:
        print("error: --graph or --auto-graph required", file=sys.stderr)
        return 2
    g = DependencyGraph.load(graph_path)
    entries = load_har(a.har)
    tree = build_base_doc(entries)
    tree = correlate(tree, g, entries)
    tree = apply_workload(tree, workloads, tg_name=_har_stem(a.har))
    write_xml(tree, a.out)
    print(f"jmx -> {a.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="e2eloader", description="HAR to JMeter JMX converter")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build-graph", help="HAR -> dependency graph JSON")
    b.add_argument("-i", "--har", required=True)
    b.add_argument("-o", "--out", default=None)
    _add_llm_args(b)
    b.set_defaults(fn=cmd_build_graph)

    s = sub.add_parser("show", help="print graph summary")
    s.add_argument("-g", "--graph", required=True)
    s.set_defaults(fn=cmd_show)

    l = sub.add_parser("list-edges", help="list edges, optionally filtered")
    l.add_argument("-g", "--graph", required=True)
    l.add_argument("--to", type=int, default=None)
    l.add_argument("--type", default=None)
    l.set_defaults(fn=cmd_list)

    ad = sub.add_parser("add-edge", help="add a dependency")
    ad.add_argument("-g", "--graph", required=True)
    ad.add_argument("--to", type=int, required=True)
    ad.add_argument("--type", required=True)
    ad.add_argument("--key", required=True, help="path segment / header / param / cookie / body key")
    ad.add_argument("--from", dest="frm", type=int, default=None)
    ad.add_argument("--resp-name", default=None)
    ad.add_argument("--resp-value", default=None)
    ad.add_argument("--resp-xpath", default=None)
    ad.add_argument("--literal", default=None)
    ad.add_argument("--csv", default=None, help="file:vars")
    ad.add_argument("--from-har-idx", type=int, default=0)
    ad.add_argument("--to-har-idx", type=int, default=0)
    ad.set_defaults(fn=cmd_add)

    d = sub.add_parser("del-edge", help="delete a dependency by id")
    d.add_argument("-g", "--graph", required=True)
    d.add_argument("--id", type=int, required=True)
    d.set_defaults(fn=cmd_del)

    e = sub.add_parser("edit-edge", help="edit key/literal of a dependency")
    e.add_argument("-g", "--graph", required=True)
    e.add_argument("--id", type=int, required=True)
    e.add_argument("--key", default=None)
    e.add_argument("--value", default=None)
    e.set_defaults(fn=cmd_edit)

    j = sub.add_parser("build-jmx", help="HAR + graph + workload -> ready JMX")
    j.add_argument("-i", "--har", required=True)
    j.add_argument("-g", "--graph", default=None)
    j.add_argument("--auto-graph", action="store_true")
    j.add_argument("-o", "--out", required=True)
    j.add_argument("--workload", action="append", default=[],
                   help="threads=20,delay=0,startup=30,hold=120,shutdown=10 (repeatable)")
    _add_llm_args(j)
    j.set_defaults(fn=cmd_build_jmx)
    return p


def main(argv=None) -> int:
    return _run(argv)


def _run(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(_run())
