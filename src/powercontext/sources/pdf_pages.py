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
import re
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from powercontext.artifacts import MemoryCitation
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import ScopedMemoryApplication


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
])
_EXPANSIONS = (
    (r"geograph|operat.*\bin\b|regions", "segments countries international markets operations"),
    (r"legal|lawsuit|battles", "litigation proceedings contingencies material"),
    (
        r"ebitda|depreciation|amortization|cash flow|capex",
        "consolidated statements cash flows operating income depreciation amortization capital expenditures",
    ),
    (r"income statement|p&l", "consolidated statements income earnings"),
)


async def search_pdf_pages(
    memory: ScopedMemoryApplication, question: str, limit: int = 10
) -> tuple[MemoryCitation, ...]:
    """Retrieve page candidates through scoped Memory, retaining only real PDF citations.

    Query expansion uses financial vocabulary, never document or question identities.
    Candidate work is bounded independently of the number of ingested pages.
    """
    from powercontext.builtin.runtime.models import GetMemoryEntryRequest, SearchMemoryRequest

    if type(limit) is not int or limit <= 0 or not question.strip():
        raise ValueError("a nonempty question and positive limit are required")  # noqa: TRY003
    terms = [term for term in re.findall(r"[a-z]+|\d+", question.lower()) if term not in _QUERY_STOP]
    queries = [" ".join(dict.fromkeys(terms)) or question]
    for pattern, expansion in _EXPANSIONS:
        if re.search(pattern, question.lower()):
            queries.append(expansion)
    scores: dict[tuple[str, str, str], float] = {}
    candidates = {}
    for query in queries:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=max(40, limit * 3)))
        for rank, hit in enumerate(result.hits, 1):
            key = (hit.memory_ref.artifact_id, hit.entry_id, hit.entry_version_id)
            candidates[key] = hit
            scores[key] = scores.get(key, 0.0) + 1 / (30 + rank)
    citations = []
    seen = set()
    for key in sorted(scores, key=lambda key: (-scores[key], key)):
        hit = candidates[key]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref, entry_id=hit.entry_id, entry_version_id=hit.entry_version_id
        )
        entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
        pages = tuple(sorted(source.source_id for source in entry.sources if source.source_type == "pdf-page"))
        if not pages or any(page in seen for page in pages):
            continue
        seen.update(pages)
        citations.append(citation)
        if len(citations) == limit:
            break
    return tuple(citations)
