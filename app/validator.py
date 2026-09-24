"""Atomic dependency validation. Port of AtomicDependencyValidator.java.

Uses OpenNLP PorterStemmer semantics; here via a bundled stemmer with
nltk/snowball override when available. Threshold LCS >= 0.44 preserved.
"""
from __future__ import annotations
from urllib.parse import urlparse
from .lcs import lc_substr_norm
from .response_model import AtomicObject, StructuredObject

LCS_DEADLINE = 0.44


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
                              father: StructuredObject | None, from_url: str) -> bool:
    if atomic.value != to_value:
        return False
    if to_name is None:
        return False
    return _name_match(to_name, atomic.name, father, from_url)


def evaluate_header_atomic_dep(atomic: AtomicObject, header_name: str, header_value: str,
                               father: StructuredObject | None, from_url: str) -> bool:
    if header_name == "Authorization":
        return atomic.value == header_value[7:]
    return general_atomic_comparison(atomic, header_value, header_name, father, from_url)


def evaluate_cookie_atomic_dep(atomic: AtomicObject, cookie_name: str, cookie_value: str,
                               father: StructuredObject | None, from_url: str) -> bool:
    if "Authorization" in cookie_name:
        return atomic.value == cookie_value
    return general_atomic_comparison(atomic, cookie_value, cookie_name, father, from_url)


def evaluate_url_atomic_dep(atomic: AtomicObject, from_url: str, father: StructuredObject | None,
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
