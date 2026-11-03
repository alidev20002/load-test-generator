"""Atomic dependency validation. Port of AtomicDependencyValidator.java.

Two name-matching strategies:

- LCS (default): exact value match, then Porter-stemmed longest-common-
  substring >= 0.44 (Java parity).
- LLM (opt-in via :func:`enable_llm`): exact value match, then a
  lightweight LLM compares the raw field names with full endpoint context.
  No stemming/preprocessing is applied on the LLM path.
"""
from __future__ import annotations
from urllib.parse import urlparse
from .lcs import lc_substr_norm
from . import llm_matcher as _LLM
from . import embedding_matcher as _EMBEDDING
from .response_model import AtomicObject, StructuredObject

LCS_DEADLINE = 0.44

# ---- LLM opt-in state (process-global; set by extractor/CLI) ----
_LLM_ENABLED = False
_LLM_CONFIG: _LLM.LLMConfig | None = None

_EMBEDDING_ENABLED = False


def enable_llm(config: _LLM.LLMConfig | None = None) -> _LLM.LLMConfig:
    """Enable the LLM name-matcher. Returns the active config."""
    global _LLM_ENABLED, _LLM_CONFIG
    _LLM_CONFIG = config or _LLM.LLMConfig.from_env()
    _LLM_ENABLED = True
    return _LLM_CONFIG

def disable_llm() -> None:
    global _LLM_ENABLED
    _LLM_ENABLED = False

def enable_embedding():
    global _EMBEDDING_ENABLED
    _EMBEDDING_ENABLED = True

def disable_embedding():
    global _EMBEDDING_ENABLED
    _EMBEDDING_ENABLED = False


def llm_enabled() -> bool:
    return _LLM_ENABLED


def active_llm_config() -> _LLM.LLMConfig:
    global _LLM_CONFIG
    if _LLM_CONFIG is None:
        _LLM_CONFIG = _LLM.LLMConfig.from_env()
    return _LLM_CONFIG


def _bundled_stem(word: str) -> str:
    """Tiny Porter-like stemmer (fallback when nltk/snowball missing)."""
    w = word.lower()
    if len(w) <= 3:
        return w
    for suf, rep in (("ization", "ize"), ("ational", "ate"), ("fulness", ""),
                     ("ousness", ""), ("iveness", ""), ("tion", "t"),
                     ("ness", ""), ("ment", ""), ("ing", ""), ("ed", ""),
                     ("ies", "i"), ("es", ""), ("s", ""), ("ly", ""),
                     ("er", ""), ("ic", ""), ("y", "i")):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)] + rep if rep else w[: -len(suf)]
    return w


try:
    from nltk.stem import PorterStemmer as _NLTKStemmer
    _NLTK = _NLTKStemmer()

    def stem(word: str) -> str:
        return _NLTK.stem(word.lower())
except Exception:
    def stem(word: str) -> str:  # type: ignore[misc]
        return _bundled_stem(word)


def id_completion_child(child: str, father: str) -> str:
    if len(child) <= 3:
        return stem(father) + child
    return child


def id_completion_request(child: str, request_url: str) -> list[str]:
    if len(child) <= 3:
        segs = [s for s in urlparse(request_url).path.split("/") if s]
        if len(segs) >= 2:
            return [stem(segs[-1]) + child, stem(segs[-2]) + child]
        if len(segs) == 1:
            return [stem(segs[-1]) + child]
        return [child]
    return [child]


def _name_match(to_name: str, atomic_name: str, father: StructuredObject | None,
                request_url: str) -> bool:
    to_s = stem(to_name.lower())
    a = atomic_name.lower()
    if father is not None and father.name != "":
        cand = id_completion_child(a, stem(father.name.lower()))
        return lc_substr_norm(to_s, cand) >= LCS_DEADLINE
    for cand in id_completion_request(a, request_url):
        if lc_substr_norm(to_s, stem(cand)) >= LCS_DEADLINE:
            return True
    return False


def general_atomic_comparison(atomic: AtomicObject, to_value: str, to_name: str | None,
                              father: StructuredObject | None, from_url: str,
                              consumer_url: str = "", location: str = "body",
                              producer_method: str = "",
                              consumer_method: str = "") -> bool:
    if atomic.value != to_value:
        return False
    if to_name is None:
        return False
    if _LLM_ENABLED:
        return _llm_name_match(atomic, father, to_name, from_url, consumer_url,
                               to_value, location, producer_method,
                               consumer_method,
                               lcs_fallback=lambda: _name_match(
                                   to_name, atomic.name, father, from_url))

    if _EMBEDDING_ENABLED:
        return _embedding_name_match(atomic, father, to_name, from_url, consumer_url,
                                       to_value, location, producer_method,
                                       consumer_method,
                                       lcs_fallback=lambda: _name_match(
                                           to_name, atomic.name, father, from_url))

    return _name_match(to_name, atomic.name, father, from_url)


def _qualified_producer_name(atomic: AtomicObject,
                             father: StructuredObject | None) -> str:
    """Full producer field label for the LLM (no stemming)."""
    if father is not None and father.name:
        return f"{father.name}.{atomic.name}"
    if atomic.xpath:
        return f"{atomic.name} ({atomic.xpath})"
    return atomic.name


def _llm_name_match(atomic: AtomicObject, father: StructuredObject | None,
                    to_name: str, from_url: str, consumer_url: str,
                    value: str, location: str, producer_method: str = "",
                    consumer_method: str = "",
                    lcs_fallback=None) -> bool:
    """Value already equal; ask the LLM about the names. Fall back to LCS."""
    cfg = active_llm_config()
    producer_endpoint = f"{producer_method} {from_url}".strip()
    consumer_endpoint = f"{consumer_method} {consumer_url}".strip()
    res = _LLM.field_match(
        producer_endpoint=producer_endpoint,
        producer_field=_qualified_producer_name(atomic, father),
        producer_value=atomic.value,
        consumer_endpoint=consumer_endpoint,
        consumer_field=to_name,
        consumer_value=value,
        location=location,
        cfg=cfg,
    )
    if res is not None:
        return res
    if cfg.fallback_to_lcs and lcs_fallback is not None:
        return bool(lcs_fallback())
    return False

def _embedding_name_match(atomic: AtomicObject, father: StructuredObject | None,
                    to_name: str, from_url: str, consumer_url: str,
                    value: str, location: str, producer_method: str = "",
                    consumer_method: str = "",
                    lcs_fallback=None) -> bool:
    producer_endpoint = f"{producer_method} {from_url}".strip()
    consumer_endpoint = f"{consumer_method} {consumer_url}".strip()
    res, score = _EMBEDDING.field_match_embedding(
        producer_endpoint=producer_endpoint,
        producer_field=_qualified_producer_name(atomic, father),
        producer_value=atomic.value,
        consumer_endpoint=consumer_endpoint,
        consumer_field=to_name,
        consumer_value=value,
    )
    if res is not None and score > 0.8:
        return res

    print("Embedding fallbacked to lcs")
    
    return bool(lcs_fallback())

def evaluate_header_atomic_dep(atomic: AtomicObject, header_name: str, header_value: str,
                               father: StructuredObject | None, from_url: str,
                               consumer_url: str = "", producer_method: str = "",
                               consumer_method: str = "") -> bool:
    if header_name == "Authorization":
        # Bearer token passthrough: name is fixed, no LLM/LCS needed.
        return atomic.value == header_value[7:]
    return general_atomic_comparison(atomic, header_value, header_name, father, from_url,
                                     consumer_url=consumer_url, location="header",
                                     producer_method=producer_method,
                                     consumer_method=consumer_method)


def evaluate_cookie_atomic_dep(atomic: AtomicObject, cookie_name: str, cookie_value: str,
                               father: StructuredObject | None, from_url: str,
                               consumer_url: str = "", producer_method: str = "",
                               consumer_method: str = "") -> bool:
    if "Authorization" in cookie_name:
        return atomic.value == cookie_value
    return general_atomic_comparison(atomic, cookie_value, cookie_name, father, from_url,
                                     consumer_url=consumer_url, location="cookie",
                                     producer_method=producer_method,
                                     consumer_method=consumer_method)


def evaluate_url_atomic_dep(atomic: AtomicObject, from_url: str, father: StructuredObject | None,
                            possible_name: str, consumer_url: str = "",
                            producer_method: str = "",
                            consumer_method: str = "") -> bool:
    if _LLM_ENABLED:
        # URL path segment has no explicit field name; the preceding static
        # segment (possible_name) is the consumer hint.
        return _llm_name_match(atomic, father, possible_name, from_url,
                               consumer_url, atomic.value, "url",
                               producer_method, consumer_method,
                               lcs_fallback=lambda: _lcs_url(atomic, from_url,
                                                             father,
                                                             possible_name))

    if _EMBEDDING_ENABLED:
        return _embedding_name_match(atomic, father, possible_name, from_url,
                                        consumer_url, atomic.value, "url",
                                        producer_method, consumer_method,
                                        lcs_fallback=lambda: _lcs_url(atomic, from_url,
                                                                        father,
                                                                        possible_name))
    return _lcs_url(atomic, from_url, father, possible_name)


def _lcs_url(atomic: AtomicObject, from_url: str, father: StructuredObject | None,
             possible_name: str) -> bool:
    to_lower = possible_name.lower()
    a = atomic.name.lower()
    if father is not None and father.name != "":
        cand = id_completion_child(a, stem(father.name.lower()))
        return lc_substr_norm(stem(to_lower), cand) >= LCS_DEADLINE
    for cand in id_completion_request(a, from_url):
        if lc_substr_norm(stem(to_lower), stem(cand)) >= LCS_DEADLINE:
            return True
    return False
