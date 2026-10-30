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
from dataclasses import dataclass, replace
from itertools import chain, combinations

from powercontext._lexical import english_stem, english_terms
from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.artifacts.search import analyze_text
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
    excluded = {english_stem(word) for word in _STOP}
    return [word for term in analyze_text(text).split() if (word := english_stem(term)) not in excluded]


def _abbreviations(text: str) -> list[tuple[str, frozenset[str]]]:
    """Align an explicit parenthesized abbreviation to its preceding long form."""
    definitions = []
    for match in re.finditer(r"\(([A-Z][A-Za-z0-9-]{1,9})\)", text):
        short = re.sub(r"[^a-z0-9]", "", match[1].casefold())
        words = list(re.finditer(r"\S+", text[: match.start()]))[-min(len(short) + 5, 2 * len(short)) :]
        if not words:
            continue
        first = words[0].start()
        long = text[first : match.start()].casefold()
        cursor = len(long) - 1
        for index in range(len(short) - 1, -1, -1):
            while cursor >= 0 and (
                long[cursor] != short[index] or (index == 0 and cursor and long[cursor - 1].isalnum())
            ):
                cursor -= 1
            if cursor < 0:
                break
            cursor -= 1
        else:
            terms = frozenset(_terms(long[cursor + 1 :]))
            if len(terms) >= 2:
                definitions.append((english_stem(short), terms))
    return definitions


def _concept_terms(text: str, aliases: Sequence[tuple[str, frozenset[str]]]) -> frozenset[str]:
    terms = set(_terms(text))
    terms.update(short for short, long in aliases if long <= terms)
    return frozenset(terms)


def _spans(text: str) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for boundary in re.finditer(r"(?<=[.!?。！？])\s+|\n+", text):  # noqa: RUF001 - Include Chinese sentence boundaries.
        # Scientific prose includes names such as "C. difficile" and "et al.".
        # Keep those in one sentence rather than treating their periods as stops.
        preceding = text[start : boundary.start()]
        if re.search(r"(?:\b[A-Za-z]|\bet al|\be\.g|\bi\.e)\.$", preceding) and re.match(
            r"[a-z]", text[boundary.end() :]
        ):
            continue
        if boundary.end() > start:
            # Boundary whitespace is still literal evidence. Dropping it can
            # make a selected sentence fail exact character-interval coverage.
            spans.append((start, boundary.end()))
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
    query_terms = set(_terms(request.query))
    aliases = list(
        dict.fromkeys(
            pair for text in (request.query, *(hit.text for _, hit in documents)) for pair in _abbreviations(text)
        )
    )
    aliases = [(short, long) for short, long in aliases if short in query_terms or long <= query_terms]
    for short, long in aliases:
        if long <= query_terms:
            query_terms.difference_update(long)
            query_terms.add(short)
    frequency: Counter[str] = Counter()
    for _, hit in documents:
        frequency.update(_concept_terms(hit.text, aliases))
    weights = _query_weights(request.query, query_terms, frequency, len(documents))
    plans = []
    for scope_id, hit in documents:
        address = MemoryEntryAddress(
            ArtifactAddress(scope_id=scope_id, artifact=hit.memory_ref), hit.entry_id, hit.entry_version_id
        )
        plans.append(_document_plans(hit.text, address, weights, request.max_bytes, aliases))
    selected = _combine_plans(plans, request.max_bytes)
    if not selected:
        return PreparedContextBuild(PreparedContext(status="empty", content=None, content_bytes=0), ())
    content = _render(selected)
    return PreparedContextBuild(
        context=PreparedContext(status="ready", content=content, content_bytes=len(content.encode("utf-8"))),
        origins=tuple(fragment.address for fragment in selected),
        fragments=tuple(selected),
    )


def _query_weights(query: str, terms: set[str], frequency: Counter[str], document_count: int) -> dict[str, float]:
    entities = {
        english_stem(word.casefold())
        for word in re.findall(r"[^\W_]+", query)
        if any(letter.isalpha() for letter in word)
        and (any(letter.isdigit() for letter in word) or (len(word) >= 2 and word.isupper()))
    }
    operators = set(
        english_terms(
            "associate association express expression activate activation increase decrease reduce promote induce regulate higher lower positive negative inhibit suppress prevent attenuate enhance impaired impair lack absence greater fewer"
        )
    )
    return {
        # A term shared by every candidate carries less distinguishing evidence
        # than a term confined to one candidate.
        term: math.log(1 + (document_count - frequency[term] + 0.5) / (frequency[term] + 0.5))
        * (2.5 if term in entities else 1.4 if term in operators else 1.0)
        for term in sorted(terms)
    }


@dataclass(frozen=True)
class _Sentence:
    fragment: PreparedMemoryFragment
    terms: frozenset[str]
    score: float


@dataclass(frozen=True)
class _Plan:
    fragments: tuple[PreparedMemoryFragment, ...]
    score: float
    cost: int


def _sentence_candidates(
    text: str,
    address: MemoryEntryAddress,
    weights: dict[str, float],
    aliases: Sequence[tuple[str, frozenset[str]]],
) -> list[_Sentence]:
    """Score literal sentences using query concepts and explicit prose sections."""
    total = sum(weights.values()) or 1.0
    sentences = []
    section = ""
    for index, (start, end) in enumerate(_spans(text)):
        body = text[start:end]
        terms = _concept_terms(body, aliases)
        score = sum(weights.get(term, 0.0) for term in sorted(terms)) / total
        markers = re.findall(
            r"\b(BACKGROUND|CONTEXT|OBJECTIVES?|METHODS?|DESIGN|RESULTS?|CONCLUSIONS?|FINDINGS)\b", body, re.I
        )
        if markers:
            section = markers[-1].upper()
        if section.startswith(("RESULT", "CONCLU", "FINDING")):
            score *= 1.5
        elif section:
            score *= 0.8
        if index == 0:
            score *= 0.7
        if re.search(r"\b(results?|conclusions?|found|showed|demonstrated|revealed|observed)\b", body, re.I):
            score *= 1.15
        sentences.append(_Sentence(PreparedMemoryFragment(address, start, end, body), terms, score))
    return sentences


def _document_plans(
    text: str,
    address: MemoryEntryAddress,
    weights: dict[str, float],
    max_bytes: int,
    aliases: Sequence[tuple[str, frozenset[str]]] = (),
) -> list[_Plan]:
    total = sum(weights.values()) or 1.0
    all_terms = _concept_terms(text, aliases)
    prior = sum(weight for term, weight in weights.items() if term in all_terms) / total
    sentences = _sentence_candidates(text, address, weights, aliases)
    alternatives = []
    envelope = len(_render(()).encode("utf-8"))
    for start in range(len(sentences)):
        for end in range(start + 1, len(sentences) + 1):
            first, last = sentences[start].fragment.start, sentences[end - 1].fragment.end
            fragment = PreparedMemoryFragment(address, first, last, text[first:last])
            cost = len(_render((fragment,)).encode("utf-8"))
            if cost > max_bytes:
                break
            score = _plan_score(sentences[start:end], prior, weights, len(fragment.text))
            if score > 0:
                alternatives.append(_Plan((fragment,), score, cost - envelope))
    alternatives.extend(_sparse_plans(sentences, text, prior, weights, max_bytes))
    alternatives.extend(_window_plans(sentences, prior, weights, max_bytes, aliases))
    frontier = []
    best_score = -1.0
    for plan in sorted(alternatives, key=lambda item: (item.cost, -item.score)):
        if plan.score > best_score:
            frontier.append(plan)
            best_score = plan.score
    return frontier


def _sparse_plans(
    sentences: Sequence[_Sentence], text: str, prior: float, weights: dict[str, float], max_bytes: int
) -> list[_Plan]:
    """Consider separate evidence regions without paying for an irrelevant gap."""
    relevant = sorted((sentence for sentence in sentences if sentence.score > 0), key=lambda item: -item.score)
    alternatives = []
    envelope = len(_render(()).encode("utf-8"))
    # A finding can span several separated sentences. Explore combinations
    # instead of requiring every larger plan to contain the top-scoring pair.
    strongest = relevant[:8]
    groups = chain(
        combinations(relevant, 2),
        combinations(strongest, 3),
        combinations(strongest[:6], 4),
    )
    for group in groups:
        included: list[PreparedMemoryFragment] = []
        for sentence in sorted(group, key=lambda item: item.fragment.start):
            included = _merge(included, sentence.fragment, text)
        cost = len(_render(included).encode("utf-8"))
        if cost <= max_bytes:
            score = _plan_score(group, prior, weights, sum(len(fragment.text) for fragment in included))
            alternatives.append(_Plan(tuple(included), score, cost - envelope))
    return alternatives


def _combine_plans(groups: Sequence[Sequence[_Plan]], max_bytes: int) -> list[PreparedMemoryFragment]:
    """Choose a cost/utility frontier, charging the envelope only once."""
    envelope = len(_render(()).encode("utf-8"))
    states: dict[int, tuple[float, tuple[PreparedMemoryFragment, ...]]] = {envelope: (0.0, ())}
    for alternatives in groups:
        updated = dict(states)
        for cost, (score, fragments) in states.items():
            for plan in alternatives:
                combined_cost = cost + plan.cost + bool(fragments)
                if combined_cost > max_bytes:
                    break
                # Prefer another complete sentence from a strong source over a
                # marginal, redundant citation; allow complementary sources.
                combined_score = score + plan.score * (0.82 if fragments else 1.0)
                if combined_score > updated.get(combined_cost, (-1.0, ()))[0]:
                    updated[combined_cost] = (combined_score, fragments + plan.fragments)
        states = {}
        best_score = -1.0
        for cost, state in sorted(updated.items()):
            if state[0] > best_score:
                states[cost] = state
                best_score = state[0]
    return list(max(states.values(), key=lambda item: item[0])[1])


def _plan_score(sentences: Sequence[_Sentence], prior: float, weights: dict[str, float], length: int) -> float:
    scores = sorted((sentence.score for sentence in sentences), reverse=True)
    if not scores or scores[0] <= 0:
        return 0.0
    common = set().union(*(sentence.terms for sentence in sentences))
    coverage = sum(weight for term, weight in weights.items() if term in common) / (sum(weights.values()) or 1.0)
    # Coverage cannot independently reward a document with weak overall relevance.
    return prior**2 * (
        scores[0] + 0.55 * sum(scores[1:4]) + 0.0004 * min(length, 1600) + 0.20 * coverage
    )


def _window_plans(
    sentences: Sequence[_Sentence],
    prior: float,
    weights: dict[str, float],
    max_bytes: int,
    aliases: Sequence[tuple[str, frozenset[str]]],
) -> list[_Plan]:
    """Let relevant oversized sentences compete with complete-sentence plans."""
    anchors = set(weights)
    anchors.update(term for short, long in aliases if short in weights for term in long)
    envelope = len(_render(()).encode("utf-8"))
    plans = []
    for sentence in sentences:
        if sentence.score <= 0 or len(_render((sentence.fragment,)).encode("utf-8")) <= max_bytes:
            continue
        full_weight = sum(weights.get(term, 0.0) for term in sentence.terms)
        for fragment in _bounded_windows(sentence.fragment, anchors, max_bytes):
            terms = _concept_terms(fragment.text, aliases)
            score = sentence.score * sum(weights.get(term, 0.0) for term in terms) / (full_weight or 1.0)
            candidate = _Sentence(fragment, terms, score)
            if score > 0:
                plans.append(
                    _Plan(
                        (fragment,),
                        _plan_score((candidate,), prior, weights, len(fragment.text)),
                        len(_render((fragment,)).encode("utf-8")) - envelope,
                    )
                )
    return plans


def _bounded_windows(
    fragment: PreparedMemoryFragment,
    query_terms: set[str],
    max_bytes: int,
) -> list[PreparedMemoryFragment]:
    """Fit literal character windows around matching original-text positions."""
    empty = replace(fragment, text="", end=fragment.start)
    room = max_bytes - len(_render((empty,)).encode("utf-8"))
    if room <= 0:
        return []
    positions = []
    for word in re.finditer(r"\S+", fragment.text):
        if set(_terms(word.group())) & query_terms:
            positions.append(word.start())
        # CJK prose need not contain spaces. Character positions remain in the
        # original string; Analyzer offsets after case folding are not reused.
        positions.extend(
            word.start() + index
            for index, character in enumerate(word.group())
            if not character.isascii() and set(_terms(character)) & query_terms
        )
    windows = {}
    for anchor in positions:
        left = fragment.text[:anchor].encode("utf-8")[-max(1, room // 3) :].decode("utf-8", errors="ignore")
        start = anchor - len(left)
        low, high = anchor + 1, min(len(fragment.text), start + room)
        best = None
        while low <= high:
            end = (low + high) // 2
            candidate = replace(
                fragment, start=fragment.start + start, end=fragment.start + end, text=fragment.text[start:end]
            )
            if len(_render((candidate,)).encode("utf-8")) <= max_bytes:
                best, low = candidate, end + 1
            else:
                high = end - 1
        if best is not None:
            windows[best.start, best.end] = best
    return list(windows.values())


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
