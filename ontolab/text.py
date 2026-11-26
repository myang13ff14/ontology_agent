"""Small shared text helpers: normalisation, tokenising, lexical similarity."""

from __future__ import annotations

import re

_STOPWORDS = frozenset(
    "a an the my our your i we it is are was be been to of in at and or but so very really just this "
    "that these those me us do does did have has had with for its there here what which who how when "
    "can could should would will on by from as if then than into about all any each".split()
)
_REWRITES = [
    (r"[‘’]", "'"),
    (r"wi[\s-]fi", "wifi"),
    (r"can'?t\b", "cannot"),
    (r"won'?t\b", "will not"),
    (r"n't\b", " not"),
]


def normalize(text: str) -> str:
    """Lowercase, unify punctuation and spacing. Used for exact/phrase comparison."""
    text = str(text).lower()
    for pattern, replacement in _REWRITES:
        text = re.sub(pattern, replacement, text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9%.]+", " ", text)).strip()


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "ly", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if len(word) > 3 and word[-1] == word[-2]:
                word = word[:-1]
            break
    return word


def tokens(text: str, keep_stopwords: bool = False) -> list[str]:
    words = re.findall(r"[a-z0-9]+", normalize(text))
    return [stem(w) for w in words if keep_stopwords or w not in _STOPWORDS]


def overlap(query: str, target: str) -> float:
    """Fraction of the target's tokens present in the query (0..1)."""
    wanted = set(tokens(target))
    if not wanted:
        return 0.0
    return len(wanted & set(tokens(query))) / len(wanted)


def contains_phrase(haystack: str, phrase: str) -> bool:
    """Whole-word phrase containment on normalised text."""
    h, p = normalize(haystack), normalize(phrase)
    return bool(p) and re.search(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])", h) is not None
