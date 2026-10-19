"""Natural ordering for item sizes: 6, 8, 10 … and XS < S < M < L < XL.

Database ordering is alphabetical ("10" before "6"), which made long size
runs (dresses, shoes, shirts) hard to scan. Lists are small (a few hundred
rows), so we sort in Python after filtering.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\d+(?:\.\d+)?|[A-Za-z]+")
_LETTER_SIZES = {"xxs": 0, "xs": 1, "s": 2, "sm": 2, "small": 2, "m": 3, "med": 3,
                 "medium": 3, "l": 4, "lg": 4, "large": 4, "xl": 5, "xxl": 6,
                 "2xl": 6, "xxxl": 7, "3xl": 7}


def natural_key(text) -> tuple:
    """A sort key that compares numbers numerically and letter sizes by size."""
    text = str(text or "").strip()
    if not text:
        return ((0, 0, ""),)
    key = []
    for token in _TOKEN.findall(text.lower()):
        if token[0].isdigit():
            key.append((1, float(token), ""))
        elif token in _LETTER_SIZES:
            key.append((1, -100 + _LETTER_SIZES[token], ""))
        else:
            key.append((2, 0, token))
    return tuple(key) or ((3, 0, text.lower()),)


def item_sort_key(item) -> tuple:
    """Category, then name, then size in natural order."""
    category = (getattr(item, "category", "") or "").lower()
    name = (getattr(item, "name", "") or "").lower()
    return (category, name, natural_key(getattr(item, "size", "")))


def sorted_items(items, by_category=True):
    if by_category:
        return sorted(items, key=item_sort_key)
    return sorted(items, key=lambda i: ((getattr(i, "name", "") or "").lower(),
                                        natural_key(getattr(i, "size", ""))))
