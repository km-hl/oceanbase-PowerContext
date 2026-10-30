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
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
    PreparedMemoryFragment,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY


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
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
root = args.data_dir
if args.max_claims < 0:
    parser.error("--max-claims must be nonnegative")
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


observed = []
for ordinal, claim in enumerate(c for c in claims if len(c["evidence"]) == 1):
    if args.max_claims and ordinal >= args.max_claims:
        break
    positive = next(iter(claim["evidence"]))
    query = claim["claim"]
    qs = set(terms(query))
    scores = Counter()
    for term in qs:
        rows = post[term]
        idf = math.log(1 + len(docs) / (1 + len(rows)))
        for did, count in rows:
            scores[did] += idf * count / (count + 2)
    negative = [did for did, _ in scores.most_common(10) if did != positive][:2]
    if len(negative) < 2:
        continue
    ids = sorted([positive, *negative], key=lambda did: hashlib.sha256((str(claim["id"]) + did).encode()).hexdigest())
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
    sentences = {i for rationale in claim["evidence"][positive] for i in rationale["sentences"]}
    expected = ranges(positive, sentences)
    merged = []
    for start, end in expected:
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    oracle = [PreparedMemoryFragment(address(positive), a, b, texts[positive][a:b]) for a, b in merged]
    gold = Counter(terms(" ".join(x.text for x in oracle)))
    for budget in (1400, 1800):
        if len(_render(oracle).encode()) > budget:
            continue
        result = PreparedContextBuilder().build_scopes_result(
            request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True),
            current_scope_id=scope,
            memory_candidates=(PreparedMemoryCandidates(scope_id=scope, hits=hits),),
        )
        if result.context.content_bytes > budget:
            raise ValueError("selection exceeded the complete wire budget")  # noqa: TRY003
        for fragment in result.fragments:
            original = next(texts[did] for did in ids if address(did) == fragment.address)
            if fragment.text != original[fragment.start : fragment.end]:
                raise ValueError("selection changed literal source text")  # noqa: TRY003
        spans = sorted((f.start, f.end) for f in result.fragments if f.address.entry_id == address(positive).entry_id)
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
            "doc": positive,
            "query": query,
        })
summary = {
    "schema": "powercontext.evidence-quality.v1",
    "requests": len(observed),
    "token_recall": sum(x["recall"] for x in observed) / max(1, len(observed)),
    "complete_fraction": sum(x["complete"] for x in observed) / max(1, len(observed)),
    "corpus_sha256": hashlib.sha256((root / "corpus.jsonl").read_bytes()).hexdigest(),
    "claims_sha256": hashlib.sha256((root / args.claims).read_bytes()).hexdigest(),
    "cases": observed,
}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, ensure_ascii=False, indent=2))
