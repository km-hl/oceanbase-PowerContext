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

"""Identity-preserving, batched HTTP scoring for Memory candidates."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision
from powercontext.builtin.inference import InferenceUsage


class _Score(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    rank: int
    score: float = Field(allow_inf_nan=False)


class _Scores(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scores: list[_Score]


class HTTPMemoryReranker:
    """Score every candidate and select from the complete pool in stable order."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if type(batch_size) is not int or batch_size <= 0:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(self, query: str, candidates: tuple[MemoryHit, ...], limit: int, /) -> MemoryRerankDecision:
        if not candidates or type(limit) is not int or not 1 <= limit <= len(candidates):
            raise ValueError("limit must be between one and the non-empty candidate count")  # noqa: TRY003
        scores: dict[int, float] = {}
        requests = 0
        for offset in range(0, len(candidates), self.batch_size):
            batch = [
                {"rank": rank, "text": candidate.text}
                for rank, candidate in enumerate(candidates[offset : offset + self.batch_size], offset + 1)
            ]
            requests += 1
            response = await self.client.post(self.endpoint, json={"query": query, "candidates": batch})
            response.raise_for_status()
            payload = response.json()
            scores.update(_batch_scores(payload, set(range(offset + 1, offset + 1 + len(batch)))))
        return MemoryRerankDecision(
            selected_ranks=tuple(sorted(scores, key=lambda rank: (-scores[rank], rank))[:limit]),
            usage=InferenceUsage(requests=requests),
        )


def _batch_scores(payload: object, expected: set[int]) -> dict[int, float]:
    scores: dict[int, float] = {}
    for item in _Scores.model_validate(payload).scores:
        rank, score = item.rank, item.score
        if rank not in expected or rank in scores:
            raise ValueError("response ranks must cover this batch exactly once")  # noqa: TRY003
        scores[rank] = score
    if set(scores) != expected:
        raise ValueError("response is missing batch ranks")  # noqa: TRY003
    return scores
