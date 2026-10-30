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

"""Measure full-PDF parsing and real Memory retrieval against public FinanceBench labels."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import re
import statistics
import tempfile
import time
from collections import Counter
from pathlib import Path

import pypdf

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class _LiteralPages:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="page", text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"], sources=(source,)
            )
            for source in request.sources
        )


def _terms(text: str) -> Counter[str]:
    return Counter(re.findall(r"[a-z]+|\d+", text.lower()))


def _load_pages(directories: list[Path]):
    pages = {}
    for directory in directories:
        for path in sorted(directory.glob("*.pdf")):
            if path.stem in pages:
                raise ValueError(f"duplicate document identity: {path.stem}")  # noqa: TRY003
            pages[path.stem] = parse_pdf_document(PDFDocumentInput(document_id=path.stem, content=path.read_bytes()))
    if not pages:
        raise ValueError("no complete PDF documents supplied")  # noqa: TRY003
    return pages


def _parse_quality(pages, cases):
    lookup = {(page.document_id, page.page_number): page for document in pages.values() for page in document}
    recalls, full_page_f1 = [], []
    for case in cases:
        for evidence in case["evidence"]:
            parsed = _terms(lookup[evidence["doc_name"], evidence["evidence_page_num"] + 1].text)
            expected, full = _terms(evidence["evidence_text"]), _terms(evidence["evidence_text_full_page"])
            recalls.append(sum((parsed & expected).values()) / sum(expected.values()))
            full_page_f1.append(2 * sum((parsed & full).values()) / (sum(parsed.values()) + sum(full.values())))
    return {"minimum_evidence_token_recall": min(recalls), "minimum_full_page_f1": min(full_page_f1)}


async def _evaluate(pages, cases, database: Path):
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
    settings = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database.as_posix()}"))
    originals = {}
    async with open_builtin_runtime(settings, candidate_pipeline=_LiteralPages()) as runtime:
        assert runtime.scopes is not None  # noqa: S101 - Benchmark invariant.
        scope = await runtime.scopes.create(
            ScopeDraft(title="PDF quality", summary="Literal complete pages", idempotency_key="pdf-quality")
        )
        await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
        for document in pages.values():
            for page in document:
                source = await registry.resolve(page)
                originals[source.name] = page
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
        while (await runtime.memory.for_scope(scope.scope_id).flush()).processed:
            pass
    results = []
    async with open_builtin_runtime(settings) as runtime:
        memory = runtime.memory.for_scope(scope.scope_id)
        for case in cases:
            started = time.perf_counter()
            citations = await search_pdf_pages(memory, case["question"], 20)
            latency = (time.perf_counter() - started) * 1000
            locations = set()
            for citation in citations:
                entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
                for reference in entry.sources:
                    page = originals[reference.source_id]
                    assert entry.text == f"{page.document_id}\n{page.text}".strip()  # noqa: S101 - Exact readback.
                    locations.add((page.document_id, page.page_number))
            assert len(locations) == len(citations) <= 20  # noqa: S101 - Distinct exact pages.
            expected = {(item["doc_name"], item["evidence_page_num"] + 1) for item in case["evidence"]}
            results.append({
                "id": case["financebench_id"],
                "document": case["doc_name"],
                "hits": len(locations & expected),
                "total": len(expected),
                "latency_ms": latency,
            })
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, action="append", required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pages = _load_pages(args.pdf_dir)
    cases = [
        json.loads(line)
        for line in args.annotations.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["doc_name"] in pages
    ]
    if not cases:
        parser.error("no public questions correspond to the supplied PDFs")
    missing = {evidence["doc_name"] for case in cases for evidence in case["evidence"]} - pages.keys()
    if missing:
        parser.error(f"evidence PDFs are missing: {sorted(missing)}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pdf-quality-", dir=args.output.parent) as directory:
        if not Path(directory).resolve().is_relative_to(args.output.parent.resolve()):
            raise ValueError("benchmark database must remain inside the output directory")  # noqa: TRY003
        results = asyncio.run(_evaluate(pages, cases, Path(directory).resolve() / "pages.db"))
    by_document = {}
    for name, document in pages.items():
        rows = [row for row in results if row["document"] == name]
        by_document[name] = {
            "pages": len(document),
            "sha256": document[0].document_sha256,
            "questions": len(rows),
            "recall_at_20": sum(row["hits"] for row in rows) / max(1, sum(row["total"] for row in rows)),
        }
    report = {
        "schema": "powercontext.pdf-quality.v1",
        "python": platform.python_version(),
        "pypdf": pypdf.__version__,
        "pages": sum(len(document) for document in pages.values()),
        "questions": len(results),
        "evidence_page_recall_at_20": sum(row["hits"] for row in results) / sum(row["total"] for row in results),
        "first_query_ms": results[0]["latency_ms"],
        "warm_p50_ms": statistics.median(row["latency_ms"] for row in results[1:] or results),
        "documents": by_document,
        "cases": results,
        "annotations_sha256": hashlib.sha256(args.annotations.read_bytes()).hexdigest(),
        **_parse_quality(pages, cases),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({key: value for key, value in report.items() if key != "cases"}, ensure_ascii=False, indent=2),
        flush=True,
    )


if __name__ == "__main__":
    main()
