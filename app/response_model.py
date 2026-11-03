"""Data models for analyzed HTTP response structures and atomic extracted fields."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union


@dataclass
class AtomicObject:
    """Atomic primitive value extracted from an HTTP response or header."""
    value: str = ""
    name: str = ""
    xpath: str | None = None
    from_set_cookie: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "atomic",
            "value": self.value,
            "name": self.name,
            "xpath": self.xpath,
            "from_set_cookie": self.from_set_cookie,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AtomicObject:
        return cls(
            value=str(data.get("value", "")),
            name=str(data.get("name", "")),
            xpath=data.get("xpath"),
            from_set_cookie=bool(data.get("from_set_cookie", False)),
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, AtomicObject):
            return False
        if self.xpath is None or other.xpath is None:
            return self.name == other.name and self.value == other.value
        return self.name == other.name and self.value == other.value and self.xpath == other.xpath

    def __repr__(self) -> str:
        if self.from_set_cookie:
            return f"[SET COOKIE] name:{self.name} value:{self.value}"
        return f"[ATOMIC] name:{self.name} value:{self.value} xpath:{self.xpath}"


@dataclass
class StructuredObject:
    """Structured JSON or XML object/array subtree from an HTTP response."""
    name: str = ""
    value: str = ""
    xpath: str = ""
    schema_string: str = ""
    objects: list[AtomicObject | StructuredObject] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "structured",
            "name": self.name,
            "value": self.value,
            "xpath": self.xpath,
            "schema_string": self.schema_string,
            "objects": [_object_to_dict(o) for o in self.objects],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StructuredObject:
        obj = cls(
            name=str(data.get("name", "")),
            value=str(data.get("value", "")),
            xpath=str(data.get("xpath", "")),
            schema_string=str(data.get("schema_string", "")),
        )
        raw_children = data.get("objects") or []
        obj.objects = [_object_from_dict(item) for item in raw_children]
        return obj

    def __repr__(self) -> str:
        return f"[STRUCTURED] name:{self.name} xpath:{self.xpath} children:{len(self.objects)}"


def _object_to_dict(obj: AtomicObject | StructuredObject | Any) -> dict[str, Any]:
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return dict(obj)


def _object_from_dict(data: dict[str, Any]) -> AtomicObject | StructuredObject:
    if data.get("kind") == "structured":
        return StructuredObject.from_dict(data)
    return AtomicObject.from_dict(data)


@dataclass
class ResponseUnstructured:
    """Top-level collection of extracted atomic and structured fields from an HTTP response."""
    objects: list[AtomicObject | StructuredObject] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"objects": [_object_to_dict(o) for o in self.objects]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResponseUnstructured:
        raw_objects = data.get("objects") or []
        return cls(objects=[_object_from_dict(item) for item in raw_objects])


ResponseObj = Union[AtomicObject, StructuredObject, ResponseUnstructured]
