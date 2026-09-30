"""Lightweight-LLM field-name matcher for smart dependency detection.

Opt-in alternative to the LCS + Porter-stemming name check in
``validator.py``. The pipeline is unchanged:

1. values must be exactly equal (checked by the caller/validator),
2. if equal, this module asks a small LLM whether the two field names
   plausibly denote the same concept, given both endpoints and the
   consumer field location.

Only stdlib is used for HTTP so no new dependencies are required.

Supported backends:
- ``ollama`` (default): local Ollama server, ``POST {endpoint}/api/generate``.
- ``openai-compatible``: any ``POST {endpoint}/v1/chat/completions`` server
  (vLLM, LM Studio, Ollama, OpenAI, ...). Bearer token read from
  ``OPENAI_API_KEY`` / ``LLM_API_KEY`` env when present.
- ``stub`` / ``predict_fn``: deterministic callable for tests / offline use.

On transport errors or unparseable answers ``field_match`` returns ``None``
so the caller (validator) can fall back to LCS or treat it as no-match.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Callable

# Canonical consumer-field locations. Accepts human variants
# ("query param", "body param", ...) and normalizes them.
LOCATION_HEADER = "header"
LOCATION_QUERY = "queryparam"
LOCATION_BODY = "body"
LOCATION_URL = "url"
LOCATION_COOKIE = "cookie"

_LOCATION_ALIASES = {
    "header": LOCATION_HEADER,
    "headers": LOCATION_HEADER,
    "queryparam": LOCATION_QUERY,
    "query_param": LOCATION_QUERY,
    "query param": LOCATION_QUERY,
    "queryparams": LOCATION_QUERY,
    "query": LOCATION_QUERY,
    "body": LOCATION_BODY,
    "bodyparam": LOCATION_BODY,
    "body_param": LOCATION_BODY,
    "body param": LOCATION_BODY,
    "bodyjson": LOCATION_BODY,
    "bodyue": LOCATION_BODY,
    "url": LOCATION_URL,
    "path": LOCATION_URL,
    "url path": LOCATION_URL,
    "ur": LOCATION_URL,  # common typo, kept for tolerance
    "cookie": LOCATION_COOKIE,
    "cookies": LOCATION_COOKIE,
}


def normalize_location(loc: str | None) -> str:
    """Normalize a location label to one of header/queryparam/body/url/cookie."""
    if not loc:
        return LOCATION_BODY
    key = str(loc).strip().lower()
    return _LOCATION_ALIASES.get(key, key)


@dataclass
class LLMConfig:
    provider: str = "ollama"  # ollama | openai-compatible | stub
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5:0.5b"
    timeout: float = 30.0
    fallback_to_lcs: bool = True
    cache_enabled: bool = True
    # Optional injection for tests/offline: fn(prompt, payload) -> "YES"/"NO"/None
    predict_fn: Callable[[str, dict], str | None] | None = None

    @staticmethod
    def from_env(**overrides) -> "LLMConfig":
        provider = overrides.get("provider") or os.getenv("LLM_PROVIDER", "ollama")
        endpoint = overrides.get("endpoint") or os.getenv(
            "LLM_ENDPOINT", os.getenv("OLLAMA_HOST", "http://localhost:11434"))
        model = overrides.get("model") or os.getenv("LLM_MODEL", "qwen2.5:0.5b")
        timeout = float(overrides.get("timeout") or os.getenv("LLM_TIMEOUT", "30"))
        fallback = overrides.get("fallback_to_lcs", True)
        return LLMConfig(provider=provider, endpoint=endpoint.rstrip("/"),
                         model=model, timeout=timeout,
                         fallback_to_lcs=bool(fallback),
                         predict_fn=overrides.get("predict_fn"))


_CACHE: dict[tuple, bool] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _truncate(v: str | None, limit: int = 200) -> str:
    v = "" if v is None else str(v)
    return v if len(v) <= limit else v[:limit] + "..."


def build_prompt(producer_endpoint: str, producer_field: str,
                 producer_value: str, consumer_endpoint: str,
                 consumer_field: str, consumer_value: str,
                 location: str) -> str:
    loc = normalize_location(location)
    return (
        "You decide whether two API fields represent the same value "
        "(dependency detection for load-test correlation).\n"
        f"Producer endpoint: {producer_endpoint}\n"
        f"Producer field: {producer_field}\n"
        f"Producer value: {_truncate(producer_value)}\n"
        f"Consumer endpoint: {consumer_endpoint}\n"
        f"Consumer field ({loc}): {consumer_field}\n"
        f"Consumer value: {_truncate(consumer_value)}\n"
        "The values are already equal. Consider ONLY whether the field names "
        "and endpoint context plausibly denote the same concept "
        "(e.g. 'id' vs 'userId' vs 'user_id' is YES; 'name' vs 'email' is NO; "
        "generic values like 'true'/'0' need strongly similar names).\n"
        "Answer with exactly one word: YES or NO."
    )


def parse_yes_no(text: str | None) -> bool | None:
    """Parse an LLM answer to True/False, None when ambiguous."""
    if not text:
        return None
    t = text.strip().upper()
    if t.startswith("YES"):
        return True
    if t.startswith("NO"):
        return False
    # Fallback: single-word occurrence anywhere (avoids matching "unknown").
    import re
    tokens = re.findall(r"\b(YES|NO)\b", t)
    if len(tokens) == 1:
        return tokens[0] == "YES"
    if tokens and all(x == tokens[0] for x in tokens):
        return tokens[0] == "YES"
    return None


def _post_json(url: str, payload: dict, timeout: float,
               headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _query_ollama(prompt: str, cfg: LLMConfig) -> str | None:
    body = _post_json(f"{cfg.endpoint}/api/generate",
                      {"model": cfg.model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0}}, cfg.timeout)
    return body.get("response")


def _query_openai_compatible(prompt: str, cfg: LLMConfig) -> str | None:
    headers: dict = {}
    token = os.getenv("OPENAI_API_KEY") or os.getenv("LLM_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = _post_json(f"{cfg.endpoint}/v1/chat/completions",
                      {"model": cfg.model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 5}, cfg.timeout, headers)
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _query(prompt: str, payload: dict, cfg: LLMConfig) -> bool | None:
    """Run the configured backend. Returns True/False/None (unknown/error)."""
    try:
        if cfg.predict_fn is not None:
            raw = cfg.predict_fn(prompt, payload)
        elif cfg.provider == "openai-compatible":
            raw = _query_openai_compatible(prompt, cfg)
        elif cfg.provider == "stub":
            return None
        else:  # default "ollama"
            raw = _query_ollama(prompt, cfg)
    except Exception:
        return None
    return parse_yes_no(raw)


def field_match(producer_endpoint: str, producer_field: str,
                producer_value: str, consumer_endpoint: str,
                consumer_field: str, consumer_value: str,
                location: str, cfg: LLMConfig | None = None) -> bool | None:
    """Return True/False when the LLM answers, None on error/ambiguity.

    Value equality is NOT re-checked here; the caller must check it first
    (mirrors the LCS path). Full endpoint strings are part of both the
    prompt and the cache key, so no stemming/preprocessing is applied.
    """
    cfg = cfg or LLMConfig.from_env()
    loc = normalize_location(location)
    key = (str(producer_endpoint), str(producer_field),
           str(consumer_endpoint), str(consumer_field), loc)
    if cfg.cache_enabled and key in _CACHE:
        return _CACHE[key]
    prompt = build_prompt(producer_endpoint, producer_field, producer_value,
                          consumer_endpoint, consumer_field, consumer_value, loc)
    payload = {"producer_endpoint": producer_endpoint,
               "producer_field": producer_field,
               "producer_value": producer_value,
               "consumer_endpoint": consumer_endpoint,
               "consumer_field": consumer_field,
               "consumer_value": consumer_value,
               "location": loc, "model": cfg.model}
    res = _query(prompt, payload, cfg)
    if res is not None and cfg.cache_enabled:
        _CACHE[key] = res
    return res
