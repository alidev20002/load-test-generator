"""Longest Common Substring (LCS) normalized similarity metric."""

from __future__ import annotations


def lc_substr_norm(a: str, b: str) -> float:
    """Calculate normalized longest common substring similarity between two strings.

    Returns the length of the longest common contiguous substring divided by
    the maximum length of the two strings, resulting in a score in [0.0, 1.0].
    Returns 0.0 if either string is empty.
    """
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0

    # Ensure b is the shorter string to minimize auxiliary array memory
    if len(a) < len(b):
        a, b = b, a

    len_a, len_b = len(a), len(b)
    prev = [0] * (len_b + 1)
    max_len = 0

    for char_a in a:
        curr = [0] * (len_b + 1)
        for j, char_b in enumerate(b, start=1):
            if char_a == char_b:
                curr[j] = prev[j - 1] + 1
                if curr[j] > max_len:
                    max_len = curr[j]
        prev = curr

    return max_len / max(len_a, len_b)
