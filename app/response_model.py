"""Response model. Mirrors Services/ResponseAnalyzer/{AtomicObject,StructuredObject,ResponseUnstructured}.java."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Union


@dataclass
class AtomicObject:
    value: str = ""
    name: str = ""
    xpath: str | None = None
    from_set_cookie: bool = False

    def to_dict(self) -> dict:
        return {"value": self.value, "name": self.name, "xpath": self.xpath,
                "from_set_cookie": self.from_set_cookie}

    @staticmethod
    def from_dict(d: dict) -> "AtomicObject":
        return AtomicObject(value=str(d.get("value", "")), name=str(d.get("name", "")),
                            xpath=d.get("xpath"), from_set_cookie=bool(d.get("from_set_cookie", False)))

    def __eq__(self, o):
        if not isinstance(o, AtomicObject):
            return False
        if self.xpath is None or o.xpath is None:
            return self.name == o.name and self.value == o.value
        return self.name == o.name and self.value == o.value and self.xpath == o.xpath

    def __repr__(self):
        if self.from_set_cookie:
            return f"[SET COOKIE] name:{self.name} value:{self.value}"
        return f"[ATOMIC] name:{self.name} value:{self.value} xpath:{self.xpath}"


@dataclass
class StructuredObject:
    name: str = ""
    value: str = ""
    xpath: str = ""
    schema_string: str = ""
    objects: list = field(default_factory=list)  # AtomicObject | StructuredObject

    def to_dict(self) -> dict:
        return {"kind": "structured", "name": self.name, "value": self.value,
                "xpath": self.xpath, "schema_string": self.schema_string,
                "objects": [_obj_to_dict(o) for o in self.objects]}

    @staticmethod
    def from_dict(d: dict) -> "StructuredObject":
        obj = StructuredObject(name=d.get("name", ""), value=d.get("value", ""),
                               xpath=d.get("xpath", ""), schema_string=d.get("schema_string", ""))
        obj.objects = [_obj_from_dict(o) for o in d.get("objects", []) or []]
        return obj

    def __repr__(self):
        return f"[STRUCTURED] name:{self.name} xpath:{self.xpath} children:{len(self.objects)}"


def _obj_to_dict(o) -> dict:
    if isinstance(o, AtomicObject):
        d = o.to_dict()
        d["kind"] = "atomic"
        return d
    d = o.to_dict()
    return d


def _obj_from_dict(d: dict):
    if d.get("kind") == "structured":
        return StructuredObject.from_dict(d)
    return AtomicObject.from_dict(d)


@dataclass
class ResponseUnstructured:
    objects: list = field(default_factory=list)  # AtomicObject | StructuredObject

    def to_dict(self) -> dict:
        return {"objects": [_obj_to_dict(o) for o in self.objects]}

    @staticmethod
    def from_dict(d: dict) -> "ResponseUnstructured":
        r = ResponseUnstructured()
        r.objects = [_obj_from_dict(o) for o in d.get("objects", []) or []]
        return r


ResponseObj = Union[AtomicObject, StructuredObject, ResponseUnstructured]
