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

"""Query-scored literal sentence selection with complete wire-budget accounting."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedMemoryCandidates,
    PreparedMemoryFragment,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_STOP = frozenset([
    "a",
    "an",
    "the",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "from",
    "by",
    "with",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "and",
    "or",
    "but",
    "not",
    "do",
    "does",
    "did",
    "have",
    "has",
    "had",
    "what",
    "which",
    "how",
    "when",
    "where",
    "who",
    "why",
    "can",
    "could",
    "would",
    "should",
    "than",
    "into",
    "about",
    "between",
    "also",
    "using",
    "used",
    "show",
    "shows",
    "study",
    "studies",
    "result",
    "results",
])


def _terms(text: str) -> list[str]:
    terms = []
    for word in re.findall(r"[^\W_]+", text.lower()):
        if word in _STOP:
            continue
        if len(word) > 5:
            for suffix in ("ation", "ing", "ed", "es", "s"):
                if word.endswith(suffix):
                    word = word[: -len(suffix)]
                    break
        terms.append(word)
    return terms


def _spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?。！？])\s+|\n+", text):  # noqa: RUF001 - Include Chinese sentence boundaries.
        if boundary.start() > start:
            spans.append((start, boundary.start()))
        start = boundary.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def _render(fragments: Sequence[PreparedMemoryFragment]) -> str:
    items = [
        {
            "citation": {
                "scope_id": item.address.memory.scope_id,
                "memory_ref": item.address.memory.artifact.model_dump(mode="json"),
                "entry_id": item.address.entry_id,
                "entry_version_id": item.address.entry_version_id,
            },
            "content": item.text,
            "start": item.start,
            "end": item.end,
        }
        for item in fragments
    ]
    body = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{body}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


def select_memory_evidence(
    request: PrepareContextRequest, groups: Sequence[PreparedMemoryCandidates]
) -> PreparedContextBuild:
    """Rank candidate sentences and admit exact intervals within the final byte budget."""
    documents = [(group.scope_id, hit) for group in groups for hit in group.hits]
    query = _terms(request.query)
    query_terms = set(query)
    frequency: Counter[str] = Counter()
    for _, hit in documents:
        frequency.update(set(_terms(hit.text)))
    weights = {term: math.log(1 + (len(documents) + 1) / (frequency[term] + 1)) for term in query_terms}
    ranked = []
    texts: dict[tuple[str, str, str], str] = {}
    for scope_id, hit in documents:
        address = MemoryEntryAddress(
            ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref), hit.entry_id, hit.entry_version_id
        )
        texts[(scope_id, hit.entry_id, hit.entry_version_id)] = hit.text
        for start, end in _spans(hit.text):
            terms = _terms(hit.text[start:end])
            common = set(terms) & query_terms
            score = sum(weights[term] for term in common)
            # Matching several query concepts beats a repeated isolated keyword.
            score *= 1 + len(common) / max(1, len(query_terms))
            score /= 1 + len(terms) / 100
            if score > 0:
                ranked.append((score, PreparedMemoryFragment(address, start, end, hit.text[start:end])))
    ranked.sort(key=lambda item: -item[0])
    selected: list[PreparedMemoryFragment] = []
    for _, fragment in ranked:
        key = (fragment.address.memory.scope_id, fragment.address.entry_id, fragment.address.entry_version_id)
        proposed = _merge(selected, fragment, texts[key])
        if len(_render(proposed).encode("utf-8")) <= request.max_bytes:
            selected = proposed
    if not selected:
        selected = _fit_long_sentence(ranked, query_terms, request.max_bytes)
    if not selected:
        return PreparedContextBuild(PreparedContext(status="empty", content=None, content_bytes=0), ())
    content = _render(selected)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in selected),
        fragments=tuple(selected),
    )


def _fit_long_sentence(
    ranked: list[tuple[float, PreparedMemoryFragment]], query_terms: set[str], max_bytes: int
) -> list[PreparedMemoryFragment]:
    """Choose a fitting literal word window when no complete sentence fits."""
    for _, fragment in ranked:
        words = list(re.finditer(r"\S+", fragment.text))
        anchors = [index for index, word in enumerate(words) if set(_terms(word.group())) & query_terms]
        for width in (64, 40, 24, 12, 4, 1):
            for anchor in anchors:
                first = max(0, anchor - width // 3)
                last = min(len(words), first + width)
                start, end = fragment.start + words[first].start(), fragment.start + words[last - 1].end()
                candidate = replace(
                    fragment, start=start, end=end, text=fragment.text[start - fragment.start : end - fragment.start]
                )
                if len(_render((candidate,)).encode("utf-8")) <= max_bytes:
                    return [candidate]
    return []


def _merge(
    selected: list[PreparedMemoryFragment], fragment: PreparedMemoryFragment, text: str
) -> list[PreparedMemoryFragment]:
    result = []
    position = len(selected)
    for index, existing in enumerate(selected):
        if existing.address == fragment.address and (
            (existing.start <= fragment.end and fragment.start <= existing.end)
            or (existing.end <= fragment.start and not text[existing.end : fragment.start].strip())
            or (fragment.end <= existing.start and not text[fragment.end : existing.start].strip())
        ):
            start, end = min(existing.start, fragment.start), max(existing.end, fragment.end)
            fragment = replace(fragment, start=start, end=end, text=text[start:end])
            position = min(position, index)
        else:
            result.append(existing)
    result.insert(position, fragment)
    return result
