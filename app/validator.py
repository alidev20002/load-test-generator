"""Atomic dependency validation using LCS similarity, LLMs, or vector embeddings."""

from __future__ import annotations

import logging
from collections.abc import Callable
from urllib.parse import urlparse

from . import config
from . import embedding_matcher as _EMBEDDING
from . import llm_matcher as _LLM
from .lcs import lc_substr_norm
from .response_model import AtomicObject, StructuredObject

logger = logging.getLogger(__name__)

LCS_THRESHOLD = 0.44

_LLM_ENABLED = False
_EMBEDDING_ENABLED = False


def enable_llm() -> None:
    """Enable LLM-based field name semantic comparison."""
    global _LLM_ENABLED
    _LLM_ENABLED = True


def disable_llm() -> None:
    """Disable LLM-based matching."""
    global _LLM_ENABLED
    _LLM_ENABLED = False


def enable_embedding() -> None:
    """Enable vector embedding-based field name matching."""
    global _EMBEDDING_ENABLED
    _EMBEDDING_ENABLED = True


def disable_embedding() -> None:
    """Disable embedding-based matching."""
    global _EMBEDDING_ENABLED
    _EMBEDDING_ENABLED = False


def llm_enabled() -> bool:
    """Check if LLM matching is active."""
    return _LLM_ENABLED


def _bundled_stem(word: str) -> str:
    """Lightweight rule-based suffix trimmer used as fallback when NLTK is absent."""
    w = word.lower()
    if len(w) <= 3:
        return w
    rules = (
        ("ization", "ize"), ("ational", "ate"), ("fulness", ""),
        ("ousness", ""), ("iveness", ""), ("tion", "t"),
        ("ness", ""), ("ment", ""), ("ing", ""), ("ed", ""),
        ("ies", "i"), ("es", ""), ("s", ""), ("ly", ""),
        ("er", ""), ("ic", ""), ("y", "i"),
    )
    for suffix, replacement in rules:
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[:-len(suffix)] + replacement
    return w


try:
    from nltk.stem import PorterStemmer as _NLTKStemmer
    _NLTK = _NLTKStemmer()

    def stem(word: str) -> str:
        return _NLTK.stem(word.lower())
except Exception:
    def stem(word: str) -> str:
        return _bundled_stem(word)


def id_completion_child(child: str, father: str) -> str:
    """Prefix child field name with stemmed parent name if child is short (<= 3 chars)."""
    if len(child) <= 3:
        return stem(father) + child
    return child


def id_completion_request(child: str, request_url: str) -> list[str]:
    """Prefix child field name with path segment hints from request URL if child is short."""
    if len(child) <= 3:
        segments = [s for s in urlparse(request_url).path.split("/") if s]
        if len(segments) >= 2:
            return [stem(segments[-1]) + child, stem(segments[-2]) + child]
        if len(segments) == 1:
            return [stem(segments[-1]) + child]
    return [child]


def _name_match(
    to_name: str,
    atomic_name: str,
    father: StructuredObject | None,
    request_url: str,
) -> bool:
    """Evaluate name similarity via Porter stemming and normalized longest common substring."""
    to_stemmed = stem(to_name.lower())
    atomic_lower = atomic_name.lower()

    if father is not None and father.name:
        candidate = id_completion_child(atomic_lower, stem(father.name.lower()))
        return lc_substr_norm(to_stemmed, candidate) >= LCS_THRESHOLD

    for candidate in id_completion_request(atomic_lower, request_url):
        if lc_substr_norm(to_stemmed, stem(candidate)) >= LCS_THRESHOLD:
            return True

    return False


def _qualified_producer_name(atomic: AtomicObject, father: StructuredObject | None) -> str:
    """Construct full hierarchical field identifier for semantic matching."""
    if father is not None and father.name:
        return f"{father.name}.{atomic.name}"
    if atomic.xpath:
        return f"{atomic.name} ({atomic.xpath})"
    return atomic.name


def general_atomic_comparison(
    atomic: AtomicObject,
    to_value: str,
    to_name: str | None,
    father: StructuredObject | None,
    from_url: str,
    consumer_url: str = "",
    location: str = "body",
    producer_method: str = "",
    consumer_method: str = "",
) -> bool:
    """Evaluate whether candidate producer and consumer fields constitute a valid dependency."""
    if atomic.value != to_value or to_name is None:
        return False

    if _LLM_ENABLED:
        return _llm_name_match(
            atomic, father, to_name, from_url, consumer_url,
            to_value, location, producer_method, consumer_method,
            lcs_fallback=lambda: _name_match(to_name, atomic.name, father, from_url),
        )

    if _EMBEDDING_ENABLED:
        return _embedding_name_match(
            atomic, father, to_name, from_url, consumer_url,
            to_value, location, producer_method, consumer_method,
            lcs_fallback=lambda: _name_match(to_name, atomic.name, father, from_url),
        )

    return _name_match(to_name, atomic.name, father, from_url)


def _llm_name_match(
    atomic: AtomicObject,
    father: StructuredObject | None,
    to_name: str,
    from_url: str,
    consumer_url: str,
    value: str,
    location: str,
    producer_method: str = "",
    consumer_method: str = "",
    lcs_fallback: Callable[[], bool] | None = None,
) -> bool:
    res = _LLM.field_match(
        producer_endpoint=f"{producer_method} {from_url}".strip(),
        producer_field=_qualified_producer_name(atomic, father),
        producer_value=atomic.value,
        consumer_endpoint=f"{consumer_method} {consumer_url}".strip(),
        consumer_field=to_name,
        consumer_value=value,
        location=location,
    )
    if res is not None:
        return res
    if config.fallback_to_lcs and lcs_fallback is not None:
        return lcs_fallback()
    return False


def _embedding_name_match(
    atomic: AtomicObject,
    father: StructuredObject | None,
    to_name: str,
    from_url: str,
    consumer_url: str,
    value: str,
    location: str,
    producer_method: str = "",
    consumer_method: str = "",
    lcs_fallback: Callable[[], bool] | None = None,
) -> bool:
    res, score = _EMBEDDING.field_match_embedding(
        producer_endpoint=f"{producer_method} {from_url}".strip(),
        producer_field=_qualified_producer_name(atomic, father),
        producer_value=atomic.value,
        consumer_endpoint=f"{consumer_method} {consumer_url}".strip(),
        consumer_field=to_name,
        consumer_value=value,
    )
    if res and score > 0.8:
        return True
    if lcs_fallback is not None:
        return lcs_fallback()
    return False


def evaluate_header_atomic_dep(
    atomic: AtomicObject,
    header_name: str,
    header_value: str,
    father: StructuredObject | None,
    from_url: str,
    consumer_url: str = "",
    producer_method: str = "",
    consumer_method: str = "",
) -> bool:
    """Evaluate dependency in HTTP request header."""
    if header_name == "Authorization" and header_value.startswith("Bearer "):
        return atomic.value == header_value[7:]
    return general_atomic_comparison(
        atomic, header_value, header_name, father, from_url,
        consumer_url=consumer_url, location="header",
        producer_method=producer_method, consumer_method=consumer_method,
    )


def evaluate_cookie_atomic_dep(
    atomic: AtomicObject,
    cookie_name: str,
    cookie_value: str,
    father: StructuredObject | None,
    from_url: str,
    consumer_url: str = "",
    producer_method: str = "",
    consumer_method: str = "",
) -> bool:
    """Evaluate dependency in HTTP cookie."""
    if "Authorization" in cookie_name:
        return atomic.value == cookie_value
    return general_atomic_comparison(
        atomic, cookie_value, cookie_name, father, from_url,
        consumer_url=consumer_url, location="cookie",
        producer_method=producer_method, consumer_method=consumer_method,
    )


def evaluate_url_atomic_dep(
    atomic: AtomicObject,
    from_url: str,
    father: StructuredObject | None,
    possible_name: str,
    consumer_url: str = "",
    producer_method: str = "",
    consumer_method: str = "",
) -> bool:
    """Evaluate dependency in URL path segment."""
    if _LLM_ENABLED:
        return _llm_name_match(
            atomic, father, possible_name, from_url,
            consumer_url, atomic.value, "url",
            producer_method, consumer_method,
            lcs_fallback=lambda: _lcs_url(atomic, from_url, father, possible_name),
        )
    if _EMBEDDING_ENABLED:
        return _embedding_name_match(
            atomic, father, possible_name, from_url,
            consumer_url, atomic.value, "url",
            producer_method, consumer_method,
            lcs_fallback=lambda: _lcs_url(atomic, from_url, father, possible_name),
        )
    return _lcs_url(atomic, from_url, father, possible_name)


def _lcs_url(
    atomic: AtomicObject,
    from_url: str,
    father: StructuredObject | None,
    possible_name: str,
) -> bool:
    to_lower = possible_name.lower()
    atomic_lower = atomic.name.lower()
    if father is not None and father.name:
        cand = id_completion_child(atomic_lower, stem(father.name.lower()))
        return lc_substr_norm(stem(to_lower), cand) >= LCS_THRESHOLD
    for cand in id_completion_request(atomic_lower, from_url):
        if lc_substr_norm(stem(to_lower), stem(cand)) >= LCS_THRESHOLD:
            return True
    return False
