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

"""Event-loop-local embedding single-flight, batching and bounded LRU reuse."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict
from dataclasses import dataclass

from powercontext.builtin.inference import (
    EmbeddingModel,
    EmbeddingResult,
    EmbeddingVector,
    InferenceUsage,
    QueryEmbeddingModel,
)


@dataclass(frozen=True)
class _Embedded:
    vector: EmbeddingVector
    batch: object
    usage: InferenceUsage


class CoalescingEmbeddingModel:
    """Share pending texts without letting a caller cancel shared provider work."""

    def __init__(
        self, model: EmbeddingModel, *, max_batch_size: int = 64, coalesce_ms: float = 5.0, cache_size: int = 1024
    ) -> None:
        if type(max_batch_size) is not int or max_batch_size <= 0:
            raise ValueError("max_batch_size must be a positive integer")  # noqa: TRY003
        if type(cache_size) is not int or cache_size < 0:
            raise ValueError("cache_size must be a nonnegative integer")  # noqa: TRY003
        if isinstance(coalesce_ms, bool) or not math.isfinite(coalesce_ms) or coalesce_ms < 0:
            raise ValueError("coalesce_ms must be finite and nonnegative")  # noqa: TRY003
        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._delay = coalesce_ms / 1000
        self._cache_size = cache_size
        self._cache: OrderedDict[tuple[bool, str], EmbeddingVector] = OrderedDict()
        self._pending: dict[tuple[bool, str], asyncio.Future[_Embedded]] = {}
        self._queued: dict[tuple[bool, str], asyncio.Future[_Embedded]] = {}
        self._dispatcher: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=False)

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return await self._embed(texts, query=isinstance(self._model, QueryEmbeddingModel))

    async def _embed(self, texts: tuple[str, ...], *, query: bool) -> EmbeddingResult:
        vectors: dict[str, EmbeddingVector] = {}
        waiting: dict[str, asyncio.Future[_Embedded]] = {}
        loop = asyncio.get_running_loop()
        for text in texts:
            key = (query, text)
            if key in self._cache:
                vectors[text] = self._cache[key]
                self._cache.move_to_end(key)
            else:
                future = self._pending.get(key)
                if future is None:
                    future = loop.create_future()
                    # All callers may cancel; still retrieve provider failures to avoid
                    # unhandled Future warnings, while retaining them for other waiters.
                    future.add_done_callback(_observe_exception)
                    self._pending[key] = future
                    self._queued[key] = future
                waiting[text] = future
        if self._queued and self._dispatcher is None:
            self._dispatcher = asyncio.create_task(self._dispatch())
        results = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
        usages: dict[object, InferenceUsage] = {}
        for text, result in zip(waiting, results, strict=True):
            vectors[text] = result.vector
            usages[result.batch] = result.usage
        return EmbeddingResult(
            vectors=tuple(vectors[text] for text in texts),
            usage=InferenceUsage(
                requests=sum(usage.requests for usage in usages.values()),
                input_tokens=_sum_tokens(tuple(usages.values()), "input_tokens"),
                output_tokens=_sum_tokens(tuple(usages.values()), "output_tokens"),
            ),
        )

    async def _dispatch(self) -> None:
        await asyncio.sleep(self._delay)
        queued, self._queued = self._queued, {}
        self._dispatcher = None
        for query in (False, True):
            keys = tuple(key for key in queued if key[0] == query)
            for offset in range(0, len(keys), self._max_batch_size):
                batch = keys[offset : offset + self._max_batch_size]
                task = asyncio.create_task(self._run_batch(batch, tuple(queued[key] for key in batch)))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def _run_batch(
        self, keys: tuple[tuple[bool, str], ...], futures: tuple[asyncio.Future[_Embedded], ...]
    ) -> None:
        try:
            texts = tuple(key[1] for key in keys)
            if keys[0][0] and isinstance(self._model, QueryEmbeddingModel):
                result = await self._model.embed_query(texts)
            else:
                result = await self._model.embed(texts)
            if len(result.vectors) != len(keys):
                raise ValueError("provider vector count does not match batch size")  # noqa: TRY003, TRY301 - Batch boundary.
            batch = object()
            for key, future, vector in zip(keys, futures, result.vectors, strict=True):
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                future.set_result(_Embedded(vector, batch, result.usage))
        except BaseException as error:
            for future in futures:
                if not future.done():
                    future.set_exception(error)
        finally:
            for key in keys:
                self._pending.pop(key, None)


def _observe_exception(future: asyncio.Future[_Embedded]) -> None:
    if not future.cancelled():
        future.exception()


def _sum_tokens(usages: tuple[InferenceUsage, ...], field: str) -> int | None:
    values = [getattr(usage, field) for usage in usages if getattr(usage, field) is not None]
    return sum(values) if values else None
