"""HAR data model. Mirrors Entity/{Har,Entry,Request,Response,...}.java (tolerant parsing)."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Header:
    name: str = ""
    value: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Header":
        return Header(name=str(d.get("name", "")), value=str(d.get("value", "")))


@dataclass
class QueryParam:
    name: str = ""
    value: str = ""

    @staticmethod
    def from_dict(d: dict) -> "QueryParam":
        return QueryParam(name=str(d.get("name", "")), value=str(d.get("value", "")))


@dataclass
class Cookie:
    name: str = ""
    value: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Cookie":
        return Cookie(name=str(d.get("name", "")), value=str(d.get("value", "")))


@dataclass
class Param:
    name: str = ""
    value: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Param":
        return Param(name=str(d.get("name", "")), value=str(d.get("value", "")))


@dataclass
class PostData:
    mimeType: str = ""
    text: str = ""
    params: list[Param] = field(default_factory=list)

    @staticmethod
    def from_dict(d: dict | None) -> "PostData | None":
        if not d:
            return None
        return PostData(
            mimeType=str(d.get("mimeType", "")),
            text=str(d.get("text", "")) if d.get("text") is not None else "",
            params=[Param.from_dict(p) for p in d.get("params", []) or []],
        )


@dataclass
class Request:
    method: str = ""
    url: str = ""
    headers: list[Header] = field(default_factory=list)
    queryParams: list[QueryParam] = field(default_factory=list)
    cookies: list[Cookie] = field(default_factory=list)
    postData: PostData | None = None

    @staticmethod
    def from_dict(d: dict) -> "Request":
        return Request(
            method=str(d.get("method", "")),
            url=str(d.get("url", "")),
            headers=[Header.from_dict(h) for h in d.get("headers", []) or []],
            queryParams=[QueryParam.from_dict(q) for q in d.get("queryString", []) or []],
            cookies=[Cookie.from_dict(c) for c in d.get("cookies", []) or []],
            postData=PostData.from_dict(d.get("postData")),
        )

    def equals(self, other: "Request") -> bool:
        """Port of Request.equals: url+method, plus postData.text for POST/PUT."""
        if not isinstance(other, Request):
            return False
        if not (self.url == other.url and self.method == other.method):
            return False
        if self.method in ("POST", "PUT"):
            a = self.postData.text if self.postData and self.postData.text else ""
            b = other.postData.text if other.postData and other.postData.text else ""
            if a != b:
                return False
        return True


@dataclass
class Content:
    mimeType: str = ""
    text: str = ""

    @staticmethod
    def from_dict(d: dict | None) -> "Content":
        if not d:
            return Content()
        t = d.get("text", "")
        return Content(mimeType=str(d.get("mimeType", "")), text=str(t) if t is not None else "")


@dataclass
class Response:
    headers: list[Header] = field(default_factory=list)
    content: Content = field(default_factory=Content)

    @staticmethod
    def from_dict(d: dict | None) -> "Response":
        if not d:
            return Response()
        return Response(
            headers=[Header.from_dict(h) for h in d.get("headers", []) or []],
            content=Content.from_dict(d.get("content")),
        )


@dataclass
class Entry:
    request: Request
    response: Response

    @staticmethod
    def from_dict(d: dict) -> "Entry":
        return Entry(
            request=Request.from_dict(d.get("request", {})),
            response=Response.from_dict(d.get("response", {})),
        )


def load_har(path: str) -> list[Entry]:
    """Load HAR file -> flat entry list. Raises on bad file."""
    import json
    with open(path, "r", encoding="utf-8") as f:
        har = json.load(f)
    raw = har.get("log", {}).get("entries", [])
    return [Entry.from_dict(e) for e in raw]
