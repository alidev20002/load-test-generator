"""LLM Matcher Approach for API Dependency Detection.

Implements LLM-assisted semantic field dependency detection using prompt
templates (version-1.0 and version-2.0), response parsing, caching, and LCS fallback,
matching the exact logic from the project source code (llm_matcher.py, prompts.py, validator.py).
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

# Ensure project modules are importable
_CURRENT_DIR = Path(__file__).resolve().parent
_SEARCH_PATHS = [
    _CURRENT_DIR,
    _CURRENT_DIR.parent,
]
for p in _SEARCH_PATHS:
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from LCS_matcher_approach import LCS_THRESHOLD, lcs_url_match, name_match

logger = logging.getLogger(__name__)

try:
    from app import config as app_config
    _CONFIG = app_config
except ImportError:
    class _MockConfig:
        llm_provider = "openai"
        llm_endpoint = "http://185.239.2.48:11434/"
        llm_apikey = ""
        model = "gemma3:4b"
        timeout = 30.0
        fallback_to_lcs = True
        cache_enabled = True
    _CONFIG = _MockConfig()

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

PROMPT_V1_JSON = """You are verifying whether two fields observed in HTTP traffic represent a producer-consumer relationship.

### API 1 (Producer / Response):
Endpoint: {producer_endpoint}
Field: <CANDIDATE>{producer_field}</CANDIDATE>
Value: {producer_value}

### API 2 (Consumer / Request):
Endpoint: {consumer_endpoint}
Location: {loc}
Field: <CANDIDATE>{consumer_field}</CANDIDATE>
Value: {consumer_value}

### Question:
Does API 2 genuinely depend on the value produced by API 1 in this field? Field names may differ lexically but refer to the same logical data item.

Respond ONLY with a JSON object in this exact format, nothing else:
{{"dependency": true or false}}
"""

PROMPT_V2_BOOLEAN = """Determine whether the producer and consumer fields are semantically related based on their names, values, and endpoints.

Producer endpoint: {producer_endpoint}
Producer field: {producer_field}

Consumer endpoint: {consumer_endpoint}
Consumer field: {consumer_field}

Return true if both fields represent the same or closely related concept and could plausibly form a producer-consumer dependency. Consider synonyms, abbreviations, and naming variations.

Return false if they represent different or unrelated concepts, even if their names look similar.
If uncertain, return true.

Output rules:
- Return exactly one lowercase boolean: true or false.
- Do not provide explanations, reasoning, punctuation, markdown, or any other text.
- Your entire response must be either true or false.
"""

LLM_PROMPTS: dict[str, str] = {
    "version-1.0": PROMPT_V1_JSON.strip(),
    "version-2.0": PROMPT_V2_BOOLEAN.strip(),
}


def get_prompt_template(version: str = "version-2.0") -> str:
    """Retrieve prompt template by version key, defaulting to version-2.0."""
    return LLM_PROMPTS.get(version, LLM_PROMPTS["version-2.0"])


def build_prompt(
    producer_endpoint: str,
    producer_field: str,
    consumer_endpoint: str,
    consumer_field: str,
    version: str = "version-2.0",
    producer_value: str = "",
    consumer_value: str = "",
    location: str = "body",
) -> str:
    """Format matching prompt using specified prompt template version."""
    template = get_prompt_template(version)
    if version == "version-1.0":
        return template.format(
            producer_endpoint=producer_endpoint,
            producer_field=producer_field,
            producer_value=producer_value,
            consumer_endpoint=consumer_endpoint,
            loc=location,
            consumer_field=consumer_field,
            consumer_value=consumer_value,
        )
    return template.format(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
    )


def parse_dependency_answer(text: str | None) -> bool | None:
    """Parse boolean response from LLM output matching project source code."""
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


def _query_ollama(prompt: str, endpoint: str, timeout: float, api_key: str, model: str) -> str | None:
    client = _get_ollama_client(endpoint, timeout, api_key)
    response = client.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options={"temperature": 0.0},
        stream=False,
    )
    return response.message.content if hasattr(response, "message") else None


def _query_openai(prompt: str, endpoint: str, timeout: float, api_key: str, model: str) -> str | None:
    client = _get_openai_client(endpoint, timeout, api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=200,
    )
    if response.choices:
        return response.choices[0].message.content
    return None


_MATCH_CACHE: dict[tuple[Any, ...], bool] = {}


def clear_cache() -> None:
    """Clear memory cache of evaluated field matches."""
    _MATCH_CACHE.clear()


def _qualified_producer_name(field_name: str, father_name: str | None = None, xpath: str | None = None) -> str:
    """Construct full hierarchical field identifier for semantic matching."""
    if father_name:
        return f"{father_name}.{field_name}"
    if xpath:
        return f"{field_name} ({xpath})"
    return field_name


def field_match(
    producer_endpoint: str,
    producer_field: str,
    producer_value: str,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: str,
    location: str = "body",
    provider: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    apikey: str | None = None,
    timeout: float | None = None,
    cache_enabled: bool | None = None,
    prompt_version: str = "version-2.0",
    custom_query_fn: Callable[[str], str | None] | None = None,
) -> bool | None:
    """Verify semantic relationship between candidate fields via LLM."""
    use_provider = provider or getattr(_CONFIG, "llm_provider", "openai")
    use_endpoint = endpoint or getattr(_CONFIG, "llm_endpoint", "http://185.239.2.48:11434/")
    use_model = model or getattr(_CONFIG, "model", "gemma3:4b")
    use_apikey = apikey if apikey is not None else getattr(_CONFIG, "llm_apikey", "")
    use_timeout = timeout if timeout is not None else getattr(_CONFIG, "timeout", 30.0)
    use_cache = cache_enabled if cache_enabled is not None else getattr(_CONFIG, "cache_enabled", True)

    cache_key = (
        use_provider,
        use_endpoint,
        use_model,
        prompt_version,
        producer_endpoint,
        producer_field,
        consumer_endpoint,
        consumer_field,
    )

    if use_cache and cache_key in _MATCH_CACHE:
        return _MATCH_CACHE[cache_key]

    prompt = build_prompt(
        producer_endpoint=producer_endpoint,
        producer_field=producer_field,
        consumer_endpoint=consumer_endpoint,
        consumer_field=consumer_field,
        version=prompt_version,
        producer_value=producer_value,
        consumer_value=consumer_value,
        location=location,
    )

    raw: str | None = None
    try:
        if custom_query_fn is not None:
            raw = custom_query_fn(prompt)
        elif use_provider.lower() == "ollama":
            raw = _query_ollama(prompt, use_endpoint, use_timeout, use_apikey, use_model)
        elif use_provider.lower() == "openai":
            raw = _query_openai(prompt, use_endpoint, use_timeout, use_apikey, use_model)
        else:
            logger.warning("Unsupported LLM provider: %s", use_provider)
            return None
    except Exception as exc:
        logger.warning("LLM request failed: %s", exc)
        return None

    result = parse_dependency_answer(raw)
    if result is not None and use_cache:
        _MATCH_CACHE[cache_key] = result

    return result


def match_fields(
    producer_endpoint: str,
    producer_field: str,
    producer_value: Any,
    consumer_endpoint: str,
    consumer_field: str,
    consumer_value: Any,
    location: str = "body",
    producer_method: str = "",
    consumer_method: str = "",
    father_name: str | None = None,
    xpath: str | None = None,
    provider: str | None = None,
    endpoint: str | None = None,
    model: str | None = None,
    apikey: str | None = None,
    timeout: float | None = None,
    prompt_version: str = "version-2.0",
    fallback_to_lcs: bool | None = None,
    lcs_threshold: float = LCS_THRESHOLD,
    cache_enabled: bool | None = None,
    custom_query_fn: Callable[[str], str | None] | None = None,
) -> bool:
    """Evaluate API dependency between fields using LLM with LCS fallback.

    Pipeline matching validator._llm_name_match:
    1. Preprocessing: Value verification and field/endpoint qualification.
       - Header Bearer auth check: if Authorization and Bearer, check value match directly.
       - Cookie auth check: if Authorization in cookie name, check value match directly.
       - Value equality: atomic.value == to_value.
    2. Prompt construction: Generate versioned LLM prompt.
    3. Cache lookup: Return cached boolean if available.
    4. LLM Query: Execute inference against Ollama or OpenAI.
    5. Postprocessing: Parse boolean response.
    6. Fallback: If result is None, fall back to LCS matching (if fallback_to_lcs=True).
    """
    if not consumer_field:
        return False

    p_str = "" if producer_value is None else str(producer_value)
    c_str = "" if consumer_value is None else str(consumer_value)

    # Special header authorization rule matching evaluate_header_atomic_dep
    if location == "header" and consumer_field == "Authorization" and c_str.startswith("Bearer "):
        if producer_value is None or consumer_value is None:
            return True
        return p_str == c_str[7:]

    # Special cookie authorization rule matching evaluate_cookie_atomic_dep
    if location == "cookie" and "Authorization" in consumer_field:
        if producer_value is None or consumer_value is None:
            return True
        return p_str == c_str

    # General value equality check
    if producer_value is not None and consumer_value is not None:
        if p_str != c_str:
            return False

    atomic_name = producer_field
    if not father_name and "." in producer_field:
        father_name, atomic_name = producer_field.rsplit(".", 1)

    qualified_producer = _qualified_producer_name(atomic_name, father_name, xpath)
    formatted_producer_ep = f"{producer_method} {producer_endpoint}".strip()
    formatted_consumer_ep = f"{consumer_method} {consumer_endpoint}".strip()

    res = field_match(
        producer_endpoint=formatted_producer_ep,
        producer_field=qualified_producer,
        producer_value=p_str,
        consumer_endpoint=formatted_consumer_ep,
        consumer_field=consumer_field,
        consumer_value=c_str,
        location=location,
        provider=provider,
        endpoint=endpoint,
        model=model,
        apikey=apikey,
        timeout=timeout,
        cache_enabled=cache_enabled,
        prompt_version=prompt_version,
        custom_query_fn=custom_query_fn,
    )

    if res is not None:
        return res

    should_fallback = fallback_to_lcs if fallback_to_lcs is not None else getattr(_CONFIG, "fallback_to_lcs", True)
    if should_fallback:
        if location == "url":
            return lcs_url_match(
                atomic_name=atomic_name,
                from_url=producer_endpoint,
                father_name=father_name,
                possible_name=consumer_field,
                threshold=lcs_threshold,
            )
        return name_match(
            to_name=consumer_field,
            atomic_name=atomic_name,
            father_name=father_name,
            request_url=producer_endpoint,
            threshold=lcs_threshold,
        )

    return False


class LLMMatcher:
    """LLM Dependency Matcher class for running benchmark testcases."""

    def __init__(
        self,
        provider: str | None = None,
        endpoint: str | None = None,
        model: str | None = None,
        apikey: str | None = None,
        timeout: float | None = None,
        prompt_version: str = "version-2.0",
        fallback_to_lcs: bool | None = None,
        lcs_threshold: float = LCS_THRESHOLD,
        cache_enabled: bool | None = None,
        custom_query_fn: Callable[[str], str | None] | None = None,
    ):
        self.provider = provider
        self.endpoint = endpoint
        self.model = model
        self.apikey = apikey
        self.timeout = timeout
        self.prompt_version = prompt_version
        self.fallback_to_lcs = fallback_to_lcs
        self.lcs_threshold = lcs_threshold
        self.cache_enabled = cache_enabled
        self.custom_query_fn = custom_query_fn
        self.name = "LLM"

    def match(self, testcase: dict[str, Any]) -> bool:
        """Run LLM approach on a benchmark testcase dictionary."""
        producer = testcase.get("producer", {})
        consumer = testcase.get("consumer", {})

        p_endpoint = producer.get("endpoint", "")
        p_field = producer.get("field", "")
        p_val = producer.get("value")
        p_method = producer.get("method", "")
        father_name = producer.get("father") or producer.get("parent")
        xpath = producer.get("xpath")

        c_endpoint = consumer.get("endpoint", "")
        c_field = consumer.get("field", "")
        c_val = consumer.get("value")
        c_method = consumer.get("method", "")
        location = consumer.get("location", "body")

        return match_fields(
            producer_endpoint=p_endpoint,
            producer_field=p_field,
            producer_value=p_val,
            consumer_endpoint=c_endpoint,
            consumer_field=c_field,
            consumer_value=c_val,
            location=location,
            producer_method=p_method,
            consumer_method=c_method,
            father_name=father_name,
            xpath=xpath,
            provider=self.provider,
            endpoint=self.endpoint,
            model=self.model,
            apikey=self.apikey,
            timeout=self.timeout,
            prompt_version=self.prompt_version,
            fallback_to_lcs=self.fallback_to_lcs,
            lcs_threshold=self.lcs_threshold,
            cache_enabled=self.cache_enabled,
            custom_query_fn=self.custom_query_fn,
        )

    def predict(self, testcase: dict[str, Any]) -> bool:
        """Alias for match()."""
        return self.match(testcase)

    def clear_cache(self) -> None:
        """Clear matcher cache."""
        clear_cache()


# Convenience module-level functions
_DEFAULT_MATCHER = LLMMatcher()


def match(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the LLM approach."""
    return _DEFAULT_MATCHER.match(testcase)


def predict(testcase: dict[str, Any]) -> bool:
    """Evaluate a testcase using the LLM approach."""
    return _DEFAULT_MATCHER.predict(testcase)


def evaluate_testcase(testcase: dict[str, Any]) -> bool:
    """Alias for match()."""
    return _DEFAULT_MATCHER.match(testcase)