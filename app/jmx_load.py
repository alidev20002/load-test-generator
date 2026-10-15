"""Final load assembly. Port of JMeterLoadCreator.java (UltimateThreadGroup)."""
from __future__ import annotations
import random
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass
class Workload:
    threads: str = "1"
    delay: str = "0"
    startup: str = "1"
    hold: str = "60"
    shutdown: str = "5"

    @staticmethod
    def parse(spec: str) -> "Workload":
        """threads=20,delay=0,startup=30,hold=120,shutdown=10"""
        kw: dict = {}
        for part in spec.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                kw[k.strip()] = v.strip()
        w = Workload()
        w.threads = kw.get("threads", w.threads)
        w.delay = kw.get("delay", kw.get("initialDelay", w.delay))
        w.startup = kw.get("startup", w.startup)
        w.hold = kw.get("hold", kw.get("holdLoadFor", w.hold))
        w.shutdown = kw.get("shutdown", kw.get("shutdownTime", w.shutdown))
        for f in ("threads", "delay", "startup", "hold", "shutdown"):
            if not getattr(w, f):
                raise ValueError(f"workload field '{f}' is required")
        return w


def _sp(p, name, text):
    e = ET.SubElement(p, "stringProp", {"name": name})
    e.text = text
    return e


def _ultimate_group(name: str, workloads: list[Workload]) -> ET.Element:
    utg = ET.Element("kg.apc.jmeter.threads.UltimateThreadGroup",
                     {"guiclass": "kg.apc.jmeter.threads.UltimateThreadGroupGui",
                      "testclass": "kg.apc.jmeter.threads.UltimateThreadGroup",
                      "testname": name, "enabled": "true"})
    root = ET.SubElement(utg, "collectionProp", {"name": "ultimatethreadgroupdata"})
    for w in workloads:
        row = ET.SubElement(root, "collectionProp", {"name": str(random.randint(0, 999999))})
        _sp(row, w.threads, w.threads)
        _sp(row, w.delay, w.delay)
        _sp(row, w.startup, w.startup)
        _sp(row, w.hold, w.hold)
        _sp(row, w.shutdown, w.shutdown)
    loop = ET.SubElement(utg, "elementProp",
                         {"name": "ThreadGroup.main_controller", "elementType": "LoopController",
                          "guiclass": "LoopControllerPanel", "testclass": "LoopController",
                          "testname": "Loop Controller", "enabled": "true"})
    b = ET.SubElement(loop, "boolProp", {"name": "LoopController.continue_forever"})
    b.text = "false"
    i = ET.SubElement(loop, "intProp", {"name": "LoopController.loops"})
    i.text = "-1"
    _sp(utg, "ThreadGroup.on_sample_error", "continue")
    return utg


def apply_workload(tree: ET.ElementTree, workloads: list[Workload],
                   tg_name: str = "Thread Group") -> ET.ElementTree:
    """Replace the standard ThreadGroup with UltimateThreadGroup + CookieManager."""
    root = tree.getroot()
    for ht in root.iter("hashTree"):
        kids = list(ht)
        for i, k in enumerate(kids):
            if k.tag == "ThreadGroup":
                utg = _ultimate_group(tg_name, workloads)
                ht[i] = utg
    # ensure CookieManager at top level (port of addCookieManager)
    top = root.find("hashTree")
    if top is not None and not any(c.tag == "CookieManager" for c in top):
        cm = ET.Element("CookieManager", {"guiclass": "CookiePanel",
                                          "testclass": "CookieManager",
                                          "testname": "HTTP Cookie Manager",
                                          "enabled": "true"})
        ET.SubElement(cm, "collectionProp", {"name": "CookieManager.cookies"})
        b1 = ET.SubElement(cm, "boolProp", {"name": "CookieManager.clearEachIteration"})
        b1.text = "true"
        b2 = ET.SubElement(cm, "boolProp", {"name": "CookieManager.controlledByThreadGroup"})
        b2.text = "false"
        top.append(cm)
    return tree
