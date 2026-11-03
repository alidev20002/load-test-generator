"""LLM-assisted field semantic matcher for API dependency correlation."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from . import config
from .prompts import get_prompt_template

try:
    from ollama import Client as OllamaClient
    _HAS_OLLAMA = True
except ImportError:
    OllamaClient = None  # type: ignore[assignment,misc]
    _HAS_OLLAMA = False

try:
    from openai import OpenAI
    _HAS_OPENAI = True
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]
    _HAS_OPENAI = False

logger = logging.getLogger(__name__)


def build_prompt(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    version: str = "version-2.0",
) -> str:
    """Format matching prompt using specified prompt template version."""
    template = get_prompt_template(version)
    return template.format(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
    )


def parse_dependency_answer(text: str | None) -> bool | None:
    """Parse boolean response from LLM output."""
    if not text:
        return None

    cleaned = text.strip().lower().strip("`'\". !\n\t")
    if "false" in cleaned:
        return False
    if "true" in cleaned:
        return True
    return None


@lru_cache(maxsize=16)
def _get_ollama_client(endpoint: str, timeout: float, api_key: str = "") -> Any:
    if not _HAS_OLLAMA or OllamaClient is None:
        raise ImportError("ollama package is required for Ollama LLM provider.")

    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    return OllamaClient(
        host=endpoint,
        timeout=timeout,
        headers=headers if headers else None,
    )


@lru_cache(maxsize=16)
def _get_openai_client(endpoint: str, timeout: float, api_key: str = "") -> Any:
    if not _HAS_OPENAI or OpenAI is None:
        raise ImportError("openai package is required for OpenAI LLM provider.")

    base_url = endpoint.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url += "/v1"

    return OpenAI(
        base_url=base_url,
        api_key=api_key or "not-needed",
        timeout=timeout,
    )


def _query_ollama(prompt: str) -> str | None:
    client = _get_ollama_client(config.llm_endpoint, config.timeout, config.llm_apikey)
    response = client.chat(
        model=config.model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
        stream=False,
    )
    return response.message.content if hasattr(response, "message") else None


def _query_openai(prompt: str) -> str | None:
    client = _get_openai_client(config.llm_endpoint, config.timeout, config.llm_apikey)
    response = client.chat.completions.create(
        model=config.model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    if response.choices:
        return response.choices[0].message.content
    return None


def _query(prompt: str) -> bool | None:
    try:
        provider = config.llm_provider.lower()
        if provider == "ollama":
            raw = _query_ollama(prompt)
        elif provider == "openai":
            raw = _query_openai(prompt)
        else:
            logger.warning("Unsupported LLM provider: %s", provider)
            return None

        return parse_dependency_answer(raw)
    except Exception as exc:
        logger.warning("LLM request failed: %s", exc)
        return None


_MATCH_CACHE: dict[tuple[Any, ...], bool] = {}


def clear_cache() -> None:
    """Clear memory cache of evaluated field matches."""
    _MATCH_CACHE.clear()


def field_match(
    producer_endpoint: str,
    producer_field: str,
    producer_value: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str,
    location: str = "body",
) -> bool | None:
    """Verify semantic relationship between candidate producer and consumer fields."""
    cache_key = (
        config.llm_provider,
        config.llm_endpoint,
        config.model,
        producer_endpoint,
        producer_field,
        consumer_endpoint,
        consumer_field,
    )

    if config.cache_enabled and cache_key in _MATCH_CACHE:
        return _MATCH_CACHE[cache_key]

    prompt = build_prompt(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
    )

    result = _query(prompt)
    if result is not None and config.cache_enabled:
        _MATCH_CACHE[cache_key] = result

    return result
