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

"""Complete PDF extraction and exact, revision-aware page evidence."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from pydantic import BaseModel, Field, field_validator

from powercontext._lexical import english_stem, english_terms
from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import ScopedMemoryApplication
    from powercontext.builtin.runtime.models import MemoryEntryRecord, MemorySearchPage


class PDFDocumentInput(BaseModel):
    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document_id must not be blank")  # noqa: TRY003
        return value


class PDFPageInput(BaseModel):
    document_id: str
    document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1, strict=True)
    text: str

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        return PDFDocumentInput.validate_document_id(value)


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page, or reject the whole document without partial output."""
    from pypdf import PdfReader

    if not value.content.startswith(b"%PDF-") or b"%%EOF" not in value.content[-1024:]:
        raise ValueError("invalid or truncated PDF")  # noqa: TRY003
    digest = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF requires a password")  # noqa: TRY003, TRY301
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id, document_sha256=digest, page_number=index, text=page.extract_text() or ""
            )
            for index, page in enumerate(reader.pages, start=1)
        )
    except Exception as error:
        raise ValueError("unable to completely parse PDF") from error  # noqa: TRY003
    if not pages:
        raise ValueError("PDF has no pages")  # noqa: TRY003
    return pages


class _PDFPageSource(Source, PDFPageInput):
    pass


class _PDFAdapter:
    name = "pdf-page"
    input_class = PDFPageInput
    source_class = _PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> _PDFPageSource:
        identity = json.dumps(
            [value.document_id, value.document_sha256, value.page_number], ensure_ascii=False, separators=(",", ":")
        )
        return _PDFPageSource(
            name="pdf:" + hashlib.sha256(identity.encode()).hexdigest(),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: _PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput.model_validate(source.model_dump())


class _PDFProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = _PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pdf-page",
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_DEFINITION = AdapterSourceDefinition[PDFPageInput, _PDFPageSource, PDFPageInput](
    _PDFAdapter(), projections=(_PDFProjection(),)
)

_QUERY_STOP = frozenset([
    "what",
    "are",
    "the",
    "that",
    "in",
    "as",
    "of",
    "has",
    "any",
    "from",
    "is",
    "if",
    "then",
    "state",
    "for",
    "and",
    "a",
    "an",
    "by",
    "to",
    "with",
    "how",
    "much",
    "was",
    "were",
    "give",
    "response",
    "question",
    "relying",
    "details",
    "shown",
    "assuming",
    "perspective",
    "investment",
    "analyst",
    "only",
    "use",
    "answer",
    "define",
    "calculate",
    "unadjusted",
    "fy",
    "does",
    "did",
    "do",
    "have",
    "had",
    "based",
    "profile",
    "reasonably",
    "healthy",
    "relevant",
    "measure",
    "metric",
    "useful",
    "like",
    "please",
    "explain",
    "why",
    "who",
    "primarily",
    "important",
    "ongoing",
])
_EXPANSIONS = (
    (r"geograph|operat.*\bin\b|regions", "geographic geographical segments countries international markets worldwide"),
    (r"legal|lawsuit|battles", "litigation proceedings contingencies material claims settlement investigations"),
    (
        r"ebitda|depreciation|amortization|cash flow|capex",
        "consolidated statements cash flows operating income depreciation amortization capital expenditures",
    ),
    (r"income statement|p&l", "consolidated statements income earnings"),
    (r"retain|retention|attrition", "retention attrition customers membership accounts"),
    (r"customer concentration|primary customers|major customers", "customers concentration revenues government"),
    (r"cyclic", "cyclical cyclicality industry demand economic"),
    (r"effective tax", "effective income tax rates"),
    (r"capital.intens", "property plant equipment capital expenditures assets"),
)


_TABLE_FACETS = (
    (
        r"income statement|p&l|ebitda|operating income|operating profit|margin|capital.intens|effective tax",
        "consolidated statement income",
    ),
    (r"cash flow|capex|ebitda|depreciation|amortization|d&a|capital.intens", "consolidated statement cash flows"),
    (
        r"balance sheet|financial position|current ratio|quick ratio|liquidity|working capital|total assets|total liabilities|capital.intens",
        "consolidated balance sheet",
    ),
)


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Combine scoped native retrieval with revision-checked offline page ranking.

    Current-head changes rebuild the derived index through the public Memory list
    API. Unchanged heads reuse a bounded weak cache. Every returned citation is
    re-read through Memory and retains its actual page Source identity.
    """
    from powercontext.builtin.runtime.models import SearchMemoryRequest

    if type(limit) is not int or limit <= 0 or not question.strip():
        raise ValueError("a nonempty question and positive limit are required")  # noqa: TRY003
    queries = _native_queries(question)
    native = [
        await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(40, limit * 3))) for query in queries
    ]
    cached = _PAGE_INDEXES.get(memory)
    if cached is None or cached.memory_ref != native[0].memory_ref or cached.scope_id != memory.scope_id:
        page = await memory.list()
        cached = _PageIndex.build(memory.scope_id, page.memory_ref, page.entries)
        if len(_PAGE_INDEXES) >= 4:
            _PAGE_INDEXES.pop(next(iter(_PAGE_INDEXES)))
        _PAGE_INDEXES[memory] = cached
    ranked = _rank_pages(cached, question, queries, native, limit)
    return await _read_page_citations(memory, cached, ranked, limit)


def _native_queries(question: str) -> list[str]:
    terms = [term for term in re.findall(r"[a-z]+", question.lower()) if term not in _QUERY_STOP]
    queries = [" ".join(dict.fromkeys(terms)) or question]
    queries.extend(expansion for pattern, expansion in _EXPANSIONS if re.search(pattern, question, re.I))
    return queries


def _rank_pages(
    index: _PageIndex, question: str, queries: list[str], native: Sequence[MemorySearchPage], limit: int
) -> list[int]:
    eligible = set(index.eligible(question))
    identities = {
        (page.citation.entry_id, page.citation.entry_version_id): position for position, page in enumerate(index.pages)
    }
    scores: dict[int, float] = {}
    for result in native:
        for rank, hit in enumerate(result.hits, 1):
            position = identities.get((hit.entry_id, hit.entry_version_id))
            if position is not None and position in eligible:
                scores[position] = scores.get(position, 0.0) + 1 / (30 + rank)
    facets = [(query, True) for pattern, query in _TABLE_FACETS if re.search(pattern, question, re.I)]
    offline = [(query, False) for query in queries] + facets
    # Statement facets admit pages that fail the broad native query's lexical
    # admission floor while retaining the native ranking's contribution.
    for query, table in offline:
        ranking = index.rank(_query_terms(query), tuple(sorted(eligible)), table=table)
        for rank, position in enumerate(ranking[: max(80, limit * 4)], 1):
            bonus = 2 if table and rank <= 3 else 1
            scores[position] = scores.get(position, 0.0) + bonus / (40 + rank)
    return sorted(scores, key=lambda position: (-scores[position], position))


async def _read_page_citations(
    memory: ScopedMemoryApplication, index: _PageIndex, ranked: list[int], limit: int
) -> tuple[MemoryCitation, ...]:
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest

    citations = []
    seen: set[str] = set()
    for position in ranked:
        citation = index.pages[position].citation
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = tuple(source.source_id for source in entry.sources if source.source_type == "pdf-page")
        if not pages or any(page in seen for page in pages):
            continue
        seen.update(pages)
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)


def _document_terms(text: str) -> list[str]:
    ignored = {english_stem(word) for word in _QUERY_STOP}
    text = re.sub(r"\bd&a\b", "depreciation amortization", text, flags=re.I)
    text = re.sub(r"\bprofits?\b", "income", text, flags=re.I)
    text = re.sub(r"statements? of (?:operations|earnings)", "statement income", text, flags=re.I)
    return [term for term in english_terms(text) if term not in ignored]


def _query_terms(text: str) -> list[str]:
    return list(dict.fromkeys(_document_terms(text)))


@dataclass(frozen=True)
class _IndexedPage:
    citation: MemoryCitation
    document_id: str
    terms: Counter[str]
    heading: tuple[str, ...]
    length: int
    numeric_density: float


@dataclass(frozen=True)
class _PageIndex:
    scope_id: str
    memory_ref: ArtifactRef | None
    pages: tuple[_IndexedPage, ...]
    frequency: Counter[str]
    average_length: float

    @classmethod
    def build(cls, scope_id: str, memory_ref: ArtifactRef | None, entries: Sequence[MemoryEntryRecord]) -> _PageIndex:
        pages = []
        frequency: Counter[str] = Counter()
        for record in entries:
            if not any(source.source_type == "pdf-page" for source in record.entry.sources):
                continue
            text = record.entry.text
            terms = Counter(_document_terms(text))
            frequency.update(terms.keys())
            words = english_terms(text)
            numeric_density = sum(word.isdecimal() for word in words) / max(1, len(words))
            pages.append(
                _IndexedPage(
                    record.citation,
                    text.split("\n", 1)[0],
                    terms,
                    tuple(_query_terms(text[:350])),
                    sum(terms.values()),
                    numeric_density,
                )
            )
        return cls(
            scope_id, memory_ref, tuple(pages), frequency, sum(page.length for page in pages) / max(1, len(pages))
        )

    def eligible(self, question: str) -> tuple[int, ...]:
        compact = re.sub(r"[\W_]", "", question.casefold())
        companies = {page.document_id.split("_", 1)[0] for page in self.pages}
        abbreviations = [word.casefold() for word in re.findall(r"\b[A-Z]{3,5}\b", question)]
        selected = set()
        for company in companies:
            name = re.sub(r"[\W_]", "", company.casefold())
            exact = len(name) >= 2 and name in compact
            abbreviated = any(name.startswith(word[:2]) and re.search(".*".join(word), name) for word in abbreviations)
            if exact or abbreviated:
                selected.add(company)
        return tuple(
            index
            for index, page in enumerate(self.pages)
            if not selected or page.document_id.split("_", 1)[0] in selected
        )

    def rank(self, query: list[str], eligible: tuple[int, ...], *, table: bool = False) -> list[int]:
        query = [term for term in query if not term.isdecimal()]
        scores = {}
        for index in eligible:
            page = self.pages[index]
            score = 0.0
            for term in query:
                count = page.terms[term]
                if count:
                    idf = math.log(1 + (len(self.pages) - self.frequency[term] + 0.5) / (self.frequency[term] + 0.5))
                    score += (
                        idf * count * 2.2 / (count + 1.2 * (0.45 + 0.55 * page.length / max(1, self.average_length)))
                    )
                    if term in page.heading:
                        score += 0.3 * idf
            if table:
                covered = sum(term in page.heading for term in query) / max(1, len(query))
                if covered == 1:
                    score *= 2 + 8 * page.numeric_density
                else:
                    score *= 1 + page.numeric_density
            if score > 0:
                scores[index] = score
        return sorted(scores, key=lambda index: (-scores[index], index))


_PAGE_INDEXES: WeakKeyDictionary[ScopedMemoryApplication, _PageIndex] = WeakKeyDictionary()
