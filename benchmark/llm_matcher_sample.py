
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ollama import Client as OllamaClient
from openai import OpenAI

from concurrent.futures import ThreadPoolExecutor

from prompts import LLM_PROMPTS


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------


@dataclass
class LLMConfig:
    provider: str = "ollama"
    endpoint: str = "http://localhost:11434"
    model: str = "qwen2.5:7b"
    api_key: str = "empty"
    timeout: float = 30.0
    fallback_to_lcs: bool = True
    cache_enabled: bool = True

# ---------------------------------------------------------------------
# Prompt and response parsing
# ---------------------------------------------------------------------

def build_prompt(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
) -> str:
    return LLM_PROMPTS["version-2.0"].format(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
    )


def parse_dependency_answer(text: str | None) -> bool | None:
    if not text:
        return None

    answer = text.strip().lower().strip("`'\". !\n\t")

    if 'false' in answer:
        return False
    else:
        return True


# ---------------------------------------------------------------------
# SDK clients
# ---------------------------------------------------------------------

def _ollama_client(
    endpoint: str,
    timeout: float,
    api_key: str,
) -> OllamaClient:
    headers = {}
    headers["Authorization"] = f"Bearer {api_key}"

    return OllamaClient(
        host=endpoint,
        timeout=timeout,
        headers=headers,
    )


@lru_cache(maxsize=16)
def _openai_client(
    endpoint: str,
    api_key: str,
    timeout: float,
) -> OpenAI:
    base_url = endpoint.rstrip("/")

    if not base_url.endswith("/v1"):
        base_url += "/v1"

    return OpenAI(
        base_url=base_url,
        api_key=api_key or "not-needed",
        timeout=timeout,
    )


def _query_ollama(prompt: str, cfg: LLMConfig) -> str | None:
    response = _ollama_client(
        endpoint=cfg.endpoint, 
        timeout=cfg.timeout, 
        api_key=cfg.api_key
    ).chat(
        model=cfg.model,
        messages=[{"role": "system", "content": prompt}],
        options={"temperature": 0},
        stream=False,
    )
    return response.message.content


def _query_openai_compatible(
    prompt: str,
    cfg: LLMConfig,
) -> str | None:

    response = _openai_client(
        cfg.endpoint, cfg.api_key, cfg.timeout
    ).chat.completions.create(
        model=cfg.model,
        messages=[{"role": "system", "content": prompt}],
        temperature=0,
        max_tokens=200,
    )

    print(response)

    if not response.choices:
        return None

    return response.choices[0].message.content


def _query(
    prompt: str,
    cfg: LLMConfig,
) -> bool | None:
    try:
        if cfg.provider == "ollama":
            raw = _query_ollama(prompt, cfg)

        elif cfg.provider == "openai":
            raw = _query_openai_compatible(prompt, cfg)

        else:
            raise ValueError(f"Unsupported LLM provider: {cfg.provider}")

        return parse_dependency_answer(raw)

    except Exception as exc:
        print(f"LLM request failed: {exc}")
        return None


# ---------------------------------------------------------------------
# Field matching
# ---------------------------------------------------------------------

_CACHE: dict[tuple, bool] = {}


def clear_cache() -> None:
    _CACHE.clear()


def field_match(
    producer_endpoint: str,
    producer_field: str,
    producer_value: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str,
    location: str,
    cfg: LLMConfig,
) -> bool | None:
    """Return True/False on a valid answer, otherwise None."""

    cache_key = (
        cfg.provider,
        cfg.endpoint,
        cfg.model,
        producer_endpoint,
        producer_field,
        consumer_endpoint,
        consumer_field,
    )

    if cfg.cache_enabled and cache_key in _CACHE:
        return _CACHE[cache_key]

    prompt = build_prompt(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
    )

    result = _query(prompt, cfg)

    if result is not None and cfg.cache_enabled:
        _CACHE[cache_key] = result

    return result



def field_match_batch(
    items: list[dict],
    cfg: LLMConfig,
    max_workers: int = 5,
) -> list[bool | None]:
    """
    Process multiple field-matching requests concurrently.

    Each item must contain:
        producer_endpoint
        producer_field
        producer_value
        consumer_endpoint
        consumer_field
        consumer_value
        location

    Returns results in the same order as input items.
    """

    def process_item(item: dict) -> bool | None:
        try:
            return field_match(
                producer_endpoint=item["producer_endpoint"],
                producer_field=item["producer_field"],
                producer_value=item["producer_value"],
                consumer_endpoint=item["consumer_endpoint"],
                consumer_field=item["consumer_field"],
                consumer_value=item["consumer_value"],
                location=item["location"],
                cfg=cfg,
            )
        except Exception as exc:
            print(f"Batch item failed: {exc}")
            return None

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        return list(executor.map(process_item, items))

