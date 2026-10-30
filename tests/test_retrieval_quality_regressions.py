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

"""Observable retrieval regressions using literal evidence and native Memory."""

import asyncio
import json

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryHit
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def test_query_inflections_select_complete_literal_evidence_within_budget():
    text = "Unrelated administrative records. " * 30 + "Calcium activated the enzymes and produced 37 units."
    hit = MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="main", revision=1),
        entry_id="e",
        entry_version_id="v",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )
    result = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query="calcium enzyme activation", evidence_selection=True, max_bytes=1000),
        current_scope_id="scope",
        memory_candidates=(PreparedMemoryCandidates(scope_id="scope", hits=(hit,)),),
    )
    assert result.context.content_bytes <= 1000
    assert any("Calcium activated the enzymes and produced 37 units." in fragment.text for fragment in result.fragments)
    for fragment, origin in zip(result.fragments, result.origins, strict=True):
        assert fragment.text == text[fragment.start : fragment.end]
        assert origin == fragment.address


def test_evidence_retains_sentence_boundary_whitespace_at_exact_wire_budget():
    evidence = "RESULTS The Q. alba strain neutralized 37 units.   \n"
    prefix = "Administrative records. " * 40
    text = prefix + evidence + prefix
    ref = ArtifactRef(family="memory", artifact_id="main", revision=1)
    hit = MemoryHit(memory_ref=ref, entry_id="e", entry_version_id="v", text=text, score=1, matched_by=("fts",))
    start, end = len(prefix), len(prefix) + len(evidence)
    item = {
        "citation": {
            "scope_id": "scope",
            "memory_ref": ref.model_dump(mode="json"),
            "entry_id": "e",
            "entry_version_id": "v",
        },
        "content": evidence,
        "start": start,
        "end": end,
    }
    body = json.dumps({"trust": "untrusted_history", "items": [item]}, ensure_ascii=False, separators=(",", ":"))
    wire = f"{TRUST_POLICY}\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{body}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
    budget = len(wire.encode("utf-8"))
    result = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query="Q. alba neutralized 37 units", evidence_selection=True, max_bytes=budget),
        current_scope_id="scope",
        memory_candidates=(PreparedMemoryCandidates(scope_id="scope", hits=(hit,)),),
    )
    assert result.context.content_bytes <= budget
    assert any(fragment.start <= start and end <= fragment.end for fragment in result.fragments)
    assert all(fragment.text == text[fragment.start : fragment.end] for fragment in result.fragments)


def test_evidence_matches_explicit_abbreviation_without_counting_its_expansion_twice():
    texts = (
        "CPK is a cellular sensor. CPK activation reversed established lung fibrosis in treated animals.",
        "Copper-activated protein kinase (CPK) activation increases cellular glucose uptake. "
        "The protein kinase regulates metabolism in activated cells.",
    )
    hits = tuple(
        MemoryHit(
            memory_ref=ArtifactRef(family="memory", artifact_id="main", revision=1),
            entry_id=str(index),
            entry_version_id="v",
            text=text,
            score=1,
            matched_by=("fts",),
        )
        for index, text in enumerate(texts)
    )
    result = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(
            query="Copper-activated protein kinase (CPK) activation increases fibrosis in lungs.",
            evidence_selection=True,
            max_bytes=700,
        ),
        current_scope_id="scope",
        memory_candidates=(PreparedMemoryCandidates(scope_id="scope", hits=hits),),
    )
    assert result.context.content_bytes <= 700
    assert any(
        fragment.address.entry_id == "0" and "reversed established lung fibrosis" in fragment.text
        for fragment in result.fragments
    )
    assert all(
        fragment.text == texts[int(fragment.address.entry_id)][fragment.start : fragment.end]
        for fragment in result.fragments
    )


class _LiteralPages:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="page", text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(source,)
            )
            for source in request.sources
        )


def test_pdf_search_filters_company_and_refreshes_after_memory_append():
    async def scenario():
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_LiteralPages()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Pages", summary="Exact report evidence", idempotency_key="pages")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            memory = runtime.memory.for_scope(scope.scope_id)
            source_ids = {}

            async def capture(document, page_number, text):
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="a" * 64, page_number=page_number, text=text)
                )
                observation = project_source_for_transport(registry, source)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )
                source_ids[source.name] = (document, page_number)
                return source.name

            await capture(
                "ALPHACO_2044_10K", 1, "Consolidated Statement of Income\nOperating income 37\nNet revenue 102\n2044"
            )
            await capture(
                "BETACO_2044_10K", 1, "Consolidated Statement of Cash Flows\nOperating cash flow 500\nDepreciation 70"
            )
            while (await memory.flush()).processed:
                pass
            question = "What is AlphaCo's EBITDA in FY2044 based on operating income and depreciation from its cash flow statement?"
            first = await search_pdf_pages(memory, question, 5)
            assert first
            added = await capture(
                "ALPHACO_2044_10K",
                2,
                "Consolidated Statement of Cash Flows\nOperating cash flow 48\nDepreciation 11\nCapital expenditures 9",
            )
            while (await memory.flush()).processed:
                pass
            second = await search_pdf_pages(memory, question, 5)
            locations = set()
            returned_sources = set()
            for citation in second:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                for source in entry.sources:
                    returned_sources.add(source.source_id)
                    locations.add(source_ids[source.source_id])
            assert added in returned_sources
            assert locations == {("ALPHACO_2044_10K", 1), ("ALPHACO_2044_10K", 2)}

    asyncio.run(scenario())
