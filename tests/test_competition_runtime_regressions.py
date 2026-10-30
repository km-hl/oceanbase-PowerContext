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

"""Additional boundary regressions beyond the published competition samples."""

import asyncio
import sqlite3

import pytest
from sqlalchemy.exc import DatabaseError

from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.inference import EmbeddingResult, InferenceUsage
from powercontext.builtin.inference.coalescing import CoalescingEmbeddingModel
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder, PreparedMemoryCandidates
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleError, SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


def test_late_embedding_waiter_survives_cancellation_with_cache_disabled():
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        class Provider:
            profile = EmbeddingProfile(profile_id="late", model="offline", dimension=1)

            async def embed(self, texts, /):
                calls.append(texts)
                started.set()
                await release.wait()
                return EmbeddingResult(vectors=((2.0,),), usage=InferenceUsage(requests=2, input_tokens=9))

        model = CoalescingEmbeddingModel(Provider(), coalesce_ms=0, cache_size=0)
        first = asyncio.create_task(model.embed(("shared",)))
        await started.wait()
        late = asyncio.create_task(model.embed(("shared", "shared")))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        result = await late
        assert result.vectors == ((2.0,), (2.0,))
        assert result.usage.requests == 2 and result.usage.input_tokens == 9
        assert calls == [("shared",)]
        await model.embed(("shared",))
        assert calls == [("shared",), ("shared",)]

    asyncio.run(scenario())


def test_embedding_failure_does_not_fail_an_independent_batch():
    async def scenario():
        calls = []

        class Provider:
            profile = EmbeddingProfile(profile_id="independent", model="offline", dimension=1)

            async def embed(self, texts, /):
                calls.append(texts)
                if texts == ("broken",):
                    raise RuntimeError("batch unavailable")  # noqa: TRY003 - Test provider failure.
                return EmbeddingResult(vectors=((1.0,),), usage=InferenceUsage(requests=1, input_tokens=3))

        model = CoalescingEmbeddingModel(Provider(), max_batch_size=1, coalesce_ms=0)
        failed, succeeded = await asyncio.gather(
            model.embed(("broken",)), model.embed(("good",)), return_exceptions=True
        )
        assert isinstance(failed, RuntimeError)
        assert isinstance(succeeded, EmbeddingResult) and succeeded.vectors == ((1.0,),)
        assert (await model.embed(("good",))).usage.requests == 0
        with pytest.raises(RuntimeError):
            await model.embed(("broken",))
        assert calls.count(("broken",)) == 2

    asyncio.run(scenario())


def test_lifecycle_database_failure_rolls_back_event_and_native_state(tmp_path):
    async def scenario():
        path = tmp_path / "atomic.db"
        settings = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"))
        async with open_builtin_runtime(settings) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Atomic", summary="Failure recovery", idempotency_key="atomic")
            )
            sid = scope.scope_id
            receipt = await runtime.sources.for_scope(sid).capture(
                CaptureSource(source_id="root", content="Original evidence", metadata={})
            )
            created = await runtime.memory.for_scope(sid).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="Retained body"),))
            )
            assert created.entry is not None
            citation = created.entry.citation
            service = runtime.lifecycle.for_scope(sid)
            await service.register_source("root", receipt.source_ref)
            await service.register_memory("fact", citation, parents=("root",))
            event = SourceLifecycleEvent(node_id="root", revision=1, sequence=1, available=False, reason="Withdraw")
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TRIGGER reject_memory_state BEFORE UPDATE ON pc_artifact_heads WHEN NEW.family = 'memory' BEGIN SELECT RAISE(ABORT, 'disk failure'); END"
                )
            with pytest.raises(DatabaseError):
                await service.apply(event)
            assert await service.resolve("fact", purpose="generation") == citation
            assert (await runtime.memory.for_scope(sid).list()).memory_ref == created.memory_ref
            with sqlite3.connect(path) as connection:
                connection.execute("DROP TRIGGER reject_memory_state")
            assert await service.apply(event) == []
            with pytest.raises(SourceLifecycleError):
                await service.resolve("fact", purpose="generation")
            assert await service.resolve("fact", purpose="audit") == citation
        async with open_builtin_runtime(settings) as runtime:
            service = runtime.lifecycle.for_scope(sid)
            before = (await runtime.memory.for_scope(sid).list()).memory_ref
            assert await service.apply(event) == []
            assert (await runtime.memory.for_scope(sid).list()).memory_ref == before
            assert await service.resolve("fact", purpose="audit") == citation

    asyncio.run(scenario())


def test_evidence_selection_finds_literal_tail_inside_an_over_budget_sentence():
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.artifacts.memory import MemoryHit

    text = "ordinary words " * 800 + "cobalt sensor calibration 37 degrees"
    hit = MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="m", revision=1),
        entry_id="e",
        entry_version_id="v",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )
    result = PreparedContextBuilder().build_scopes_result(
        request=PrepareContextRequest(query="cobalt sensor calibration", max_bytes=1200, evidence_selection=True),
        current_scope_id="scope",
        memory_candidates=(PreparedMemoryCandidates(scope_id="scope", hits=(hit,)),),
    )
    assert result.fragments
    assert result.context.content_bytes <= 1200
    assert any("37 degrees" in fragment.text for fragment in result.fragments)
    for fragment in result.fragments:
        assert fragment.text == text[fragment.start : fragment.end]
        assert fragment.address.memory.scope_id == "scope"


def test_closing_during_count_admission_releases_native_sqlite_lock(tmp_path):
    from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
    from powercontext.builtin.persistence.sqlite import SQLiteProfile
    from powercontext.builtin.persistence.tables import BUILTIN_TABLES
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor

    async def scenario():
        path = tmp_path / "closing.db"
        entered = asyncio.Event()

        async def qualify(connection, scopes):
            entered.set()
            # Keep a real driver statement in progress while shutdown starts.
            await connection.exec_driver_sql(
                "WITH RECURSIVE numbers(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM numbers WHERE x<1000000) "
                "SELECT sum(x) FROM numbers"
            )
            return frozenset()

        class Launcher:
            async def start(self, assignment):
                raise AssertionError("No scopes were admitted")  # noqa: TRY003 - Unexpected test execution.

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"), tables=BUILTIN_TABLES
        ) as profile:
            async with profile.database.transaction() as connection:
                await ArtifactProcessingIntentRepository().mark_dirty(connection, "scope", "count-test")
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(
                    ArtifactProcessingBinding("count-test", "memory", Launcher(), source_count_scope_filter=qualify),
                ),
                lease_mode="single-process",
            )
            startup = asyncio.create_task(supervisor.start())
            await entered.wait()
            await asyncio.sleep(0.005)
            await supervisor.close()
            await startup
        with sqlite3.connect(path, timeout=1) as connection:
            connection.execute("CREATE TABLE external_check(value INTEGER)")

    asyncio.run(scenario())
