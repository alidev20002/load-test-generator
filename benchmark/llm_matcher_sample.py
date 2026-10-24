"""
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
import urllib.request
from dataclasses import dataclass
from typing import Callable

from prompts import LLM_PROMPTS

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
        """Build config from configs.json (legacy name kept for compatibility).

        Optional ``overrides`` (provider/endpoint/model/timeout/
        fallback_to_lcs/...) win over the file. Env vars are resolved
        through :mod:`app.config` so ``configs.json`` stays the single
        source of truth.
        """
        from . import config as _cfg

        s = _cfg.get_llm_settings()
        provider = overrides.get("provider") or s.get("provider", "ollama")
        endpoint = overrides.get("endpoint") or s.get("endpoint") or _cfg.get_env(
            "LLM_ENDPOINT", _cfg.get_env("OLLAMA_HOST", "http://localhost:11434"))
        model = overrides.get("model") or s.get("model", "qwen2.5:0.5b")
        timeout = float(overrides.get("timeout", s.get("timeout", 30)) or 30)
        fallback = overrides.get("fallback_to_lcs", s.get("fallback_to_lcs", True))
        cache = overrides.get("cache_enabled", s.get("cache_enabled", True))
        return LLMConfig(provider=provider, endpoint=endpoint.rstrip("/"),
                         model=model, timeout=timeout,
                         fallback_to_lcs=bool(fallback),
                         cache_enabled=bool(cache),
                         predict_fn=overrides.get("predict_fn"))

    @staticmethod
    def from_config(**overrides) -> "LLMConfig":
        return LLMConfig.from_env(**overrides)


_CACHE: dict[tuple, bool] = {}


def clear_cache() -> None:
    _CACHE.clear()


def build_prompt(producer_endpoint: str, producer_field: str,
                 producer_value: str, consumer_endpoint: str,
                 consumer_field: str, consumer_value: str,
                 location: str) -> str:
    loc = normalize_location(location)
    return LLM_PROMPTS.get('version-1.0').format(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field, 
        producer_value=producer_value, 
        consumer_endpoint=consumer_endpoint, 
        loc=location,
        consumer_field=consumer_field, 
        consumer_value=consumer_value
    )


def parse_dependency_answer(text: str | None) -> bool | None:
    """Parse an LLM answer to True/False, True when ambiguous."""
    if not text:
        return True

    return "true" in text.lower()


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
    from . import config as _cfg

    headers: dict = {}
    token = _cfg.get_env("OPENAI_API_KEY") or _cfg.get_env("LLM_API_KEY")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = _post_json(f"{cfg.endpoint}/v1/chat/completions",
                      {"model": cfg.model,
                       "messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 200}, cfg.timeout, headers)
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
    return parse_dependency_answer(raw)


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
    print(f"producer_endpoint: {producer_endpoint}, producer_field: {producer_field}, producer_value: {producer_value}, consumer_endpoint: {consumer_endpoint}, consumer_field: {consumer_field}, consumer_value: {consumer_value}, loc: {loc}")
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
