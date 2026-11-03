"""Data models representing HTTP Archive (HAR) specification structures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Header:
    """HTTP header name-value pair."""
    name: str = ""
    value: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Header:
        if not data:
            return cls()
        return cls(name=str(data.get("name", "")), value=str(data.get("value", "")))


@dataclass
class QueryParam:
    """URL query parameter name-value pair."""
    name: str = ""
    value: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> QueryParam:
        if not data:
            return cls()
        return cls(name=str(data.get("name", "")), value=str(data.get("value", "")))


@dataclass
class Cookie:
    """HTTP cookie name-value pair."""
    name: str = ""
    value: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Cookie:
        if not data:
            return cls()
        return cls(name=str(data.get("name", "")), value=str(data.get("value", "")))


@dataclass
class Param:
    """Form or multipart parameter."""
    name: str = ""
    value: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Param:
        if not data:
            return cls()
        return cls(name=str(data.get("name", "")), value=str(data.get("value", "")))


@dataclass
class PostData:
    """HTTP request POST payload."""
    mimeType: str = ""
    text: str = ""
    params: list[Param] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> PostData | None:
        if not data:
            return None
        raw_params = data.get("params") or []
        parsed_params = [
            Param.from_dict(p) if isinstance(p, dict) else p
            for p in raw_params
        ]
        return cls(
            mimeType=str(data.get("mimeType", "")),
            text=str(data.get("text", "")) if data.get("text") is not None else "",
            params=parsed_params,
        )


@dataclass
class Request:
    """HTTP Request definition."""
    method: str = ""
    url: str = ""
    headers: list[Header] = field(default_factory=list)
    queryParams: list[QueryParam] = field(default_factory=list)
    cookies: list[Cookie] = field(default_factory=list)
    postData: PostData | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Request:
        headers = [
            Header.from_dict(h) if isinstance(h, dict) else h
            for h in (data.get("headers") or [])
        ]
        cookies = [
            Cookie.from_dict(c) if isinstance(c, dict) else c
            for c in (data.get("cookies") or [])
        ]

        # Extract missing cookies from the 'Cookie' header if present
        seen_cookies = {(c.name, c.value) for c in cookies}
        for h in headers:
            if (h.name or "").lower() == "cookie":
                for item in (h.value or "").split(";"):
                    item = item.strip()
                    if not item:
                        continue
                    if "=" in item:
                        name, _, val = item.partition("=")
                        name, val = name.strip(), val.strip()
                    else:
                        name, val = item, ""
                    if not name:
                        continue
                    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                        val = val[1:-1]
                    if (name, val) not in seen_cookies:
                        seen_cookies.add((name, val))
                        cookies.append(Cookie(name=name, value=val))

        raw_query = data.get("queryString") or []
        query_params = [
            QueryParam.from_dict(q) if isinstance(q, dict) else q
            for q in raw_query
        ]

        return cls(
            method=str(data.get("method", "")),
            url=str(data.get("url", "")),
            headers=headers,
            queryParams=query_params,
            cookies=cookies,
            postData=PostData.from_dict(data.get("postData")),
        )

    def equals(self, other: Any) -> bool:
        """Check semantic equality based on URL, method, and payload."""
        if not isinstance(other, Request):
            return False
        if self.url != other.url or self.method != other.method:
            return False
        if self.method in ("POST", "PUT"):
            self_text = self.postData.text if self.postData and self.postData.text else ""
            other_text = other.postData.text if other.postData and other.postData.text else ""
            if self_text != other_text:
                return False
        return True

    def __eq__(self, other: Any) -> bool:
        return self.equals(other)


@dataclass
class Content:
    """HTTP Response body content."""
    mimeType: str = ""
    text: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Content:
        if not data:
            return cls()
        text_val = data.get("text", "")
        return cls(
            mimeType=str(data.get("mimeType", "")),
            text=str(text_val) if text_val is not None else "",
        )


@dataclass
class Response:
    """HTTP Response definition."""
    headers: list[Header] = field(default_factory=list)
    content: Content = field(default_factory=Content)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Response:
        if not data:
            return cls()
        headers = [
            Header.from_dict(h) if isinstance(h, dict) else h
            for h in (data.get("headers") or [])
        ]
        return cls(
            headers=headers,
            content=Content.from_dict(data.get("content")),
        )


@dataclass
class Entry:
    """HAR log entry consisting of a request and corresponding response."""
    request: Request
    response: Response

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        return cls(
            request=Request.from_dict(data.get("request", {})),
            response=Response.from_dict(data.get("response", {})),
        )


def load_har(path: str | Path) -> list[Entry]:
    """Parse a HAR JSON file and return a list of Entry objects."""
    file_path = Path(path)
    with file_path.open("r", encoding="utf-8") as f:
        har_data = json.load(f)
    entries_data = har_data.get("log", {}).get("entries", [])
    return [Entry.from_dict(item) for item in entries_data]
