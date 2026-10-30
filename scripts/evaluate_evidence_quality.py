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

"""Reproduce a public SciFact budget-selection experiment; not a platform grader."""

import argparse
import asyncio
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from powercontext.artifacts import ArtifactAddress, ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryHit
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    GetMemoryEntryRequest,
    PrepareContextRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
    PreparedMemoryFragment,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


def _render(fragments):
    items = [
        {
            "citation": {
                "scope_id": fragment.address.memory.scope_id,
                "memory_ref": fragment.address.memory.artifact.model_dump(mode="json"),
                "entry_id": fragment.address.entry_id,
                "entry_version_id": fragment.address.entry_version_id,
            },
            "content": fragment.text,
            "start": fragment.start,
            "end": fragment.end,
        }
        for fragment in fragments
    ]
    body = json.dumps({"trust": "untrusted_history", "items": items}, ensure_ascii=False, separators=(",", ":"))
    return f"{TRUST_POLICY}\nBEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n{body}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"


parser = argparse.ArgumentParser(
    description="Evaluate literal evidence selection with public SciFact annotations outside the product runtime."
)
parser.add_argument(
    "--data-dir", type=Path, required=True, help="Directory containing corpus.jsonl and claim JSONL files"
)
parser.add_argument("--claims", default="claims_dev.jsonl")
parser.add_argument("--max-claims", type=int, default=0)
parser.add_argument("--skip-claims", type=int, default=0)
parser.add_argument("--save-candidates", type=Path)
parser.add_argument("--native", action="store_true", help="Use real capture, Memory search, citations and readback")
parser.add_argument(
    "--all-candidates",
    action="store_true",
    help="Pass all three persisted abstracts rather than the FTS-admitted subset",
)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.data_dir
if args.max_claims < 0 or args.skip_claims < 0:
    parser.error("claim counts must be nonnegative")
if args.all_candidates and not args.native:
    parser.error("--all-candidates requires --native")
docs = [json.loads(x) for x in (root / "corpus.jsonl").read_text(encoding="utf-8").splitlines()]
claims = [json.loads(x) for x in (root / args.claims).read_text(encoding="utf-8").splitlines()]


def terms(x):
    return re.findall(r"[a-z]+|\d+", x.lower())


byid = {str(d["doc_id"]): d for d in docs}
texts = {k: d["title"] + " " + " ".join(d["abstract"]) for k, d in byid.items()}
post = defaultdict(list)
for did, text in texts.items():
    for term, count in Counter(terms(text)).items():
        post[term].append((did, count))
head = ArtifactRef(family="memory", artifact_id="default", revision=1)
scope = "scope_" + ("a" * 32)


def address(did):
    return MemoryEntryAddress(
        ArtifactAddress(scope_id=scope, artifact=head),
        "me_" + hashlib.sha256(did.encode()).hexdigest()[:32],
        "mev_" + hashlib.sha256((did + "v").encode()).hexdigest()[:32],
    )


def ranges(did, sentences):
    d = byid[did]
    cursor = len(d["title"]) + 1
    out = []
    for i, text in enumerate(d["abstract"]):
        if i in sentences:
            out.append((cursor, cursor + len(text)))
        cursor += len(text) + 1
    return out


class _LiteralPipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(kind="document", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


async def _native_group(runtime, ordinal, query, ids):
    assert runtime.scopes is not None  # noqa: S101 - Native benchmark invariant.
    scope = await runtime.scopes.create(
        ScopeDraft(title="Evidence quality", summary="Literal abstracts", idempotency_key=f"evidence-{ordinal}")
    )
    sources = runtime.sources.for_scope(scope.scope_id)
    memory = runtime.memory.for_scope(scope.scope_id)
    for did in ids:
        await sources.capture(
            CaptureSource(source_id="document-" + did, content=texts[did], metadata={"document_id": did})
        )
    while (await memory.flush()).processed:
        pass
    listing = await memory.list()
    by_text = {texts[did]: did for did in ids}
    addresses = {
        by_text[record.entry.text]: MemoryEntryAddress(
            ArtifactAddress(scope_id=scope.scope_id, artifact=record.citation.memory_ref),
            record.citation.entry_id,
            record.citation.entry_version_id,
        )
        for record in listing.entries
    }
    page = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=16))
    hits = page.hits
    if args.all_candidates:
        retrieved = {hit.entry_id: hit for hit in page.hits}
        hits = tuple(
            MemoryHit(
                memory_ref=record.citation.memory_ref,
                entry_id=record.entry.entry_id,
                entry_version_id=record.entry.entry_version_id,
                text=record.entry.text,
                score=retrieved[record.entry.entry_id].score if record.entry.entry_id in retrieved else 0,
                matched_by=retrieved[record.entry.entry_id].matched_by if record.entry.entry_id in retrieved else (),
            )
            for record in listing.entries
        )
    return scope.scope_id, hits, addresses


def _candidate_ids(claim, positive):
    qs = set(terms(claim["claim"]))
    scores = Counter()
    for term in qs:
        rows = post[term]
        idf = math.log(1 + len(docs) / (1 + len(rows)))
        for did, count in rows:
            scores[did] += idf * count / (count + 2)
    negative = [did for did, _ in scores.most_common() if did != positive and len(texts[did].encode("utf-8")) <= 8192][
        :2
    ]
    return sorted([positive, *negative], key=lambda did: hashlib.sha256((str(claim["id"]) + did).encode()).hexdigest())


async def _verify_result(result, budget, ids, addresses, runtime, current_scope):
    if (
        result.context.content_bytes != len((result.context.content or "").encode("utf-8"))
        or result.context.content_bytes > budget
    ):
        raise ValueError("selection exceeded the complete wire budget")  # noqa: TRY003
    if tuple(fragment.address for fragment in result.fragments) != result.origins:
        raise ValueError("selection origins do not match literal fragments")  # noqa: TRY003
    for fragment in result.fragments:
        original = next(texts[did] for did in ids if addresses[did] == fragment.address)
        if (
            fragment.address.memory.scope_id != current_scope
            or fragment.text != original[fragment.start : fragment.end]
        ):
            raise ValueError("selection changed literal source text")  # noqa: TRY003
    if args.native:
        memory = runtime.memory.for_scope(current_scope)
        for fragment in result.fragments:
            citation = MemoryCitation(
                memory_ref=fragment.address.memory.artifact,
                entry_id=fragment.address.entry_id,
                entry_version_id=fragment.address.entry_version_id,
            )
            entry = (await memory.get(GetMemoryEntryRequest(citation=citation))).entry
            if entry.text[fragment.start : fragment.end] != fragment.text:
                raise ValueError("real Memory readback differs from selected fragment")  # noqa: TRY003


def _oracle(claim, positive, addresses):
    sentences = {i for rationale in claim["evidence"][positive] for i in rationale["sentences"]}
    expected = ranges(positive, sentences)
    merged = []
    for start, end in expected:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    oracle = [PreparedMemoryFragment(addresses[positive], a, b, texts[positive][a:b]) for a, b in merged]
    gold = Counter(terms(" ".join(x.text for x in oracle)))
    return expected, oracle, gold


def _selected_intervals(fragments, entry_id):
    """Merge overlap and adjacent intervals before counting source characters."""
    merged = []
    for start, end in sorted(
        (fragment.start, fragment.end) for fragment in fragments if fragment.address.entry_id == entry_id
    ):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


async def _evaluate():
    async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_LiteralPipeline()) as runtime:
        observed = []
        candidate_sets = []
        for ordinal, claim in enumerate(c for c in claims if len(c["evidence"]) == 1):
            if ordinal < args.skip_claims:
                continue
            if args.max_claims and ordinal >= args.skip_claims + args.max_claims:
                break
            positive = next(iter(claim["evidence"]))
            if len(texts[positive].encode("utf-8")) > 8192:
                continue
            query = claim["claim"]
            ids = _candidate_ids(claim, positive)
            hits = tuple(
                MemoryHit(
                    memory_ref=head,
                    entry_id=address(did).entry_id,
                    entry_version_id=address(did).entry_version_id,
                    text=texts[did],
                    score=1 / (i + 1),
                    matched_by=("fts",),
                )
                for i, did in enumerate(ids)
            )
            addresses = {did: address(did) for did in ids}
            current_scope = scope
            if args.native:
                current_scope, hits, addresses = await _native_group(runtime, ordinal, query, ids)
            expected, oracle, gold = _oracle(claim, positive, addresses)
            candidate_sets.append({
                "claim": claim["id"],
                "query": query,
                "scope": current_scope,
                "hits": [hit.model_dump(mode="json") for hit in hits],
                "positive": addresses[positive].entry_id,
                "expected": expected,
                "original": texts[positive],
                "oracle_bytes": len(_render(oracle).encode()),
            })
            for budget in (1400, 1800):
                if len(_render(oracle).encode()) > budget:
                    continue
                result = PreparedContextBuilder().build_scopes_result(
                    request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True),
                    current_scope_id=current_scope,
                    memory_candidates=(
                        PreparedMemoryCandidates(
                            scope_id=current_scope, memory_ref=hits[0].memory_ref if hits else None, hits=hits
                        ),
                    ),
                )
                await _verify_result(result, budget, ids, addresses, runtime, current_scope)
                spans = _selected_intervals(result.fragments, addresses[positive].entry_id)
                selected = Counter(terms(" ".join(texts[positive][a:b] for a, b in spans)))
                recall = sum((selected & gold).values()) / sum(gold.values())
                complete = all(any(start <= a and b <= end for start, end in spans) for a, b in expected)
                observed.append({
                    "claim": claim["id"],
                    "split": args.claims,
                    "budget": budget,
                    "recall": recall,
                    "complete": complete,
                    "positive_selected": bool(spans),
                    "positive_retrieved": any(hit.entry_id == addresses[positive].entry_id for hit in hits),
                    "candidate_count": len(hits),
                    "doc": positive,
                    "query": query,
                    "selected": [
                        {"entry_id": f.address.entry_id, "start": f.start, "end": f.end, "text": f.text}
                        for f in result.fragments
                    ],
                })
        if args.save_candidates:
            args.save_candidates.parent.mkdir(parents=True, exist_ok=True)
            args.save_candidates.write_text(
                json.dumps(candidate_sets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        return observed


def _group_metrics(observations):
    """Report fixed 24-claim groups instead of hiding variation in a corpus average."""
    by_claim = {}
    for row in observations:
        by_claim.setdefault(row["claim"], []).append(row)
    dual_budget = [rows for rows in by_claim.values() if {row["budget"] for row in rows} == {1400, 1800}]
    groups = []
    for offset in range(0, len(dual_budget) - 23, 24):
        rows = [row for pair in dual_budget[offset : offset + 24] for row in pair]
        groups.append({
            "claim_offset": offset,
            "requests": len(rows),
            "token_recall": sum(row["recall"] for row in rows) / len(rows),
            "complete_fraction": sum(row["complete"] for row in rows) / len(rows),
        })
    return {
        "groups_of_24_claims": groups,
        "dual_budget_claims": len(dual_budget),
        "ungrouped_tail_claims": len(dual_budget) % 24,
    }


observed = asyncio.run(_evaluate())
summary = {
    "schema": "powercontext.evidence-quality.v1",
    "requests": len(observed),
    "skip_claims": args.skip_claims,
    "max_claims": args.max_claims,
    "candidate_mode": "native_all" if args.all_candidates else "native_search" if args.native else "synthetic",
    "positive_retrieved_fraction": sum(x["positive_retrieved"] for x in observed) / max(1, len(observed)),
    "minimum_candidate_count": min((row["candidate_count"] for row in observed), default=0),
    "token_recall": sum(x["recall"] for x in observed) / max(1, len(observed)),
    "complete_fraction": sum(x["complete"] for x in observed) / max(1, len(observed)),
    "corpus_sha256": hashlib.sha256((root / "corpus.jsonl").read_bytes()).hexdigest(),
    "claims_sha256": hashlib.sha256((root / args.claims).read_bytes()).hexdigest(),
    **_group_metrics(observed),
    "cases": observed,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, ensure_ascii=False, indent=2))
