"""Workload models and Ultimate Thread Group generator for JMeter load testing."""

from __future__ import annotations

import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Workload:
    """Workload schedule specifications for JMeter thread groups."""
    threads: str = "1"
    delay: str = "0"
    startup: str = "1"
    hold: str = "60"
    shutdown: str = "5"

    @classmethod
    def parse(cls, spec: str) -> Workload:
        """Parse comma-separated workload configuration (e.g. threads=20,delay=0,startup=30,hold=120,shutdown=10)."""
        params: dict[str, str] = {}
        for part in spec.split(","):
            if "=" in part:
                key, val = part.split("=", 1)
                params[key.strip()] = val.strip()

        workload = cls(
            threads=params.get("threads", "1"),
            delay=params.get("delay", params.get("initialDelay", "0")),
            startup=params.get("startup", "1"),
            hold=params.get("hold", params.get("holdLoadFor", "60")),
            shutdown=params.get("shutdown", params.get("shutdownTime", "5")),
        )

        for field_name in ("threads", "delay", "startup", "hold", "shutdown"):
            if not getattr(workload, field_name):
                raise ValueError(f"Workload parameter '{field_name}' cannot be empty")

        return workload


def _add_string_prop(parent: ET.Element, name: str, text: str) -> ET.Element:
    elem = ET.SubElement(parent, "stringProp", {"name": name})
    elem.text = text
    return elem


def _build_ultimate_group(name: str, workloads: list[Workload]) -> ET.Element:
    """Construct a JMeter UltimateThreadGroup element configured with workload schedules."""
    utg = ET.Element(
        "kg.apc.jmeter.threads.UltimateThreadGroup",
        {
            "guiclass": "kg.apc.jmeter.threads.UltimateThreadGroupGui",
            "testclass": "kg.apc.jmeter.threads.UltimateThreadGroup",
            "testname": name,
            "enabled": "true",
        },
    )
    root_coll = ET.SubElement(utg, "collectionProp", {"name": "ultimatethreadgroupdata"})

    for w in workloads:
        row = ET.SubElement(root_coll, "collectionProp", {"name": str(random.randint(0, 999999))})
        _add_string_prop(row, w.threads, w.threads)
        _add_string_prop(row, w.delay, w.delay)
        _add_string_prop(row, w.startup, w.startup)
        _add_string_prop(row, w.hold, w.hold)
        _add_string_prop(row, w.shutdown, w.shutdown)

    loop = ET.SubElement(
        utg,
        "elementProp",
        {
            "name": "ThreadGroup.main_controller",
            "elementType": "LoopController",
            "guiclass": "LoopControllerPanel",
            "testclass": "LoopController",
            "testname": "Loop Controller",
            "enabled": "true",
        },
    )
    bool_elem = ET.SubElement(loop, "boolProp", {"name": "LoopController.continue_forever"})
    bool_elem.text = "false"
    int_elem = ET.SubElement(loop, "intProp", {"name": "LoopController.loops"})
    int_elem.text = "-1"

    _add_string_prop(utg, "ThreadGroup.on_sample_error", "continue")
    return utg


def apply_workload(
    tree: ET.ElementTree,
    workloads: list[Workload],
    tg_name: str = "Thread Group",
) -> ET.ElementTree:
    """Replace default ThreadGroup with UltimateThreadGroup and ensure top-level CookieManager."""
    root = tree.getroot()
    for ht in root.iter("hashTree"):
        children = list(ht)
        for i, child in enumerate(children):
            if child.tag == "ThreadGroup":
                utg = _build_ultimate_group(tg_name, workloads)
                ht[i] = utg

    top_hash_tree = root.find("hashTree")
    if top_hash_tree is not None and not any(c.tag == "CookieManager" for c in top_hash_tree):
        cm = ET.Element(
            "CookieManager",
            {
                "guiclass": "CookiePanel",
                "testclass": "CookieManager",
                "testname": "HTTP Cookie Manager",
                "enabled": "true",
            },
        )
        ET.SubElement(cm, "collectionProp", {"name": "CookieManager.cookies"})
        b1 = ET.SubElement(cm, "boolProp", {"name": "CookieManager.clearEachIteration"})
        b1.text = "true"
        b2 = ET.SubElement(cm, "boolProp", {"name": "CookieManager.controlledByThreadGroup"})
        b2.text = "false"
        top_hash_tree.append(cm)

    return tree
