"""Longest-common-substring similarity. Port of Services/Dependencies/LCS.java."""


def lc_substr_norm(a: str, b: str) -> float:
    """max length of common substring / max(len(a), len(b)). 0.0 if either empty."""
    m, n = len(a), len(b)
    if m == 0 or n == 0:
        return 0.0
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        cur = [0] * (n + 1)
        ai = a[i - 1]
        for j in range(1, n + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best / max(m, n)
