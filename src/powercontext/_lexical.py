# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Small, dependency-free English lexical normalization for offline evidence ranking."""

from __future__ import annotations

import re
from functools import lru_cache
from itertools import pairwise


def english_terms(text: str) -> list[str]:
    """Tokenize without losing numeric identifiers and normalize English inflections."""
    return [english_stem(word) for word in re.findall(r"[^\W_]+", text.casefold())]


def _consonants(word: str) -> list[bool]:
    result: list[bool] = []
    for letter in word:
        result.append(not result[-1] if letter == "y" and result else letter not in "aeiou")
    return result


def _measure(word: str) -> int:
    flags = _consonants(word)
    return sum(not left and right for left, right in pairwise(flags))


def _cvc(word: str) -> bool:
    return len(word) >= 3 and _consonants(word)[-3:] == [True, False, True] and word[-1] not in "wxy"


_STEP_TWO = (
    ("ational", "ate"),
    ("tional", "tion"),
    ("enci", "ence"),
    ("anci", "ance"),
    ("izer", "ize"),
    ("abli", "able"),
    ("alli", "al"),
    ("entli", "ent"),
    ("eli", "e"),
    ("ousli", "ous"),
    ("ization", "ize"),
    ("ation", "ate"),
    ("ator", "ate"),
    ("alism", "al"),
    ("iveness", "ive"),
    ("fulness", "ful"),
    ("ousness", "ous"),
    ("aliti", "al"),
    ("iviti", "ive"),
    ("biliti", "ble"),
    ("logi", "log"),
)
_STEP_THREE = (
    ("icate", "ic"),
    ("ative", ""),
    ("alize", "al"),
    ("iciti", "ic"),
    ("ical", "ic"),
    ("ful", ""),
    ("ness", ""),
)
_STEP_FOUR = (
    "al",
    "ance",
    "ence",
    "er",
    "ic",
    "able",
    "ible",
    "ant",
    "ement",
    "ment",
    "ent",
    "ion",
    "ou",
    "ism",
    "ate",
    "iti",
    "ous",
    "ive",
    "ize",
)


@lru_cache(maxsize=32768)
def english_stem(word: str) -> str:  # noqa: C901 - Ordered Porter steps share one evolving word.
    """Apply the Porter suffix-removal rules; leave non-English identifiers intact."""
    if len(word) <= 2 or not word.isascii() or not word.isalpha():
        return word
    word = _plural(word)
    word = _past_participle(word)
    if word.endswith("y") and not all(_consonants(word[:-1])):
        word = word[:-1] + "i"
    for replacements in (_STEP_TWO, _STEP_THREE):
        for suffix, replacement in replacements:
            if word.endswith(suffix):
                stem = word[: -len(suffix)]
                if _measure(stem) > 0:
                    word = stem + replacement
                break
    for suffix in _STEP_FOUR:
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if _measure(stem) > 1 and (suffix != "ion" or stem.endswith(("s", "t"))):
                word = stem
            break
    if word.endswith("e"):
        stem = word[:-1]
        measure = _measure(stem)
        if measure > 1 or (measure == 1 and not _cvc(stem)):
            word = stem
    if word.endswith("ll") and _measure(word) > 1:
        word = word[:-1]
    return word


def _plural(word: str) -> str:
    if word.endswith("sses"):
        return word[:-2]
    if word.endswith("ies"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _past_participle(word: str) -> str:
    if word.endswith("eed"):
        return word[:-1] if _measure(word[:-3]) > 0 else word
    for suffix in ("ed", "ing"):
        if word.endswith(suffix):
            stem = word[: -len(suffix)]
            if all(_consonants(stem)):
                return word
            if stem.endswith(("at", "bl", "iz")):
                return stem + "e"
            if len(stem) >= 2 and stem[-1] == stem[-2] and _consonants(stem)[-1] and stem[-1] not in "lsz":
                return stem[:-1]
            if _measure(stem) == 1 and _cvc(stem):
                return stem + "e"
            return stem
    return word
