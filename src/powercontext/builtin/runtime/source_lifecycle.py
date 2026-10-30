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

"""Persistent evidence availability with atomic native Memory state transitions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory import MemoryService
    from powercontext.builtin.runtime.application import BuiltinRuntime
    from powercontext.builtin.runtime.relational import RelationalContexts


class SourceLifecycleError(ValueError):
    """A conflicting registration, invalid dependency or unavailable reference."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    node_id: str
    revision: int = Field(gt=0)
    sequence: int = Field(gt=0)
    available: bool
    reason: str

    @field_validator("node_id", "reason")
    @classmethod
    def validate_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("event identity and reason must not be blank")  # noqa: TRY003
        return value


class _Node(BaseModel):
    node_id: str
    kind: Literal["source", "memory", "topic"]
    reference: SourceRef | MemoryCitation | ArtifactRef
    parents: tuple[str, ...] = ()
    policy: Literal["all", "any"] = "all"
    revision: int = 1
    available: bool = True
    event: SourceLifecycleEvent | None = None


class SourceLifecycleApplication:
    def __init__(self, runtime: BuiltinRuntime, contexts: RelationalContexts) -> None:
        self.runtime = runtime
        self.contexts = contexts

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycle:
        return ScopedSourceLifecycle(self, validate_scope_id(scope_id))


class ScopedSourceLifecycle:
    """Single-writer scope DAG; one transaction commits graph and native states."""

    def __init__(self, application: SourceLifecycleApplication, scope_id: str) -> None:
        self.application = application
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int = 1) -> None:
        if type(revision) is not int or revision <= 0:
            raise SourceLifecycleError("revision must be a positive integer")  # noqa: TRY003 - Public validation error detail.
        await self._register(_Node(node_id=node_id, kind="source", reference=source_ref, revision=revision))

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: Literal["all", "any"] = "all"
    ) -> None:
        self._validate_dependencies(node_id, parents, policy)
        await self._register(_Node(node_id=node_id, kind="memory", reference=citation, parents=parents, policy=policy))

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] = "all",
    ) -> None:
        self._validate_dependencies(node_id, parents, policy)
        if artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("expected a Topic Memory reference")  # noqa: TRY003 - Public validation error detail.
        await self._register(
            _Node(node_id=node_id, kind="topic", reference=artifact_ref, parents=parents, policy=policy)
        )

    @staticmethod
    def _validate_dependencies(node_id: str, parents: tuple[str, ...], policy: str) -> None:
        if not parents or len(set(parents)) != len(parents) or node_id in parents or policy not in ("all", "any"):
            raise SourceLifecycleError("invalid parents or support policy")  # noqa: TRY003 - Public validation error detail.

    async def _register(self, node: _Node) -> None:
        if not node.node_id.strip() or len(node.node_id) > 256:
            raise SourceLifecycleError("node identity must contain 1 to 256 characters")  # noqa: TRY003 - Public validation error detail.
        runtime, contexts = self.application.runtime, self.application.contexts
        async with (
            runtime._scope_operation(self.scope_id),
            runtime._locked(self.scope_id),
            contexts.database.transaction() as connection,
        ):
            nodes = await self._load(connection)
            existing = nodes.get(node.node_id)
            if existing is not None:
                if existing.model_copy(update={"available": True, "event": None}) != node:
                    raise SourceLifecycleError("node already has a different binding")  # noqa: TRY003 - Public validation error detail.
                return
            if any(parent not in nodes for parent in node.parents):
                raise SourceLifecycleError("parents must be registered before their children")  # noqa: TRY003 - Public validation error detail.
            services = contexts._services_for(self.scope_id)
            _, catalog = services.sources(connection)
            memory = services.memory(catalog, connection)
            await self._validate_reference(connection, node, memory)
            if node.parents:
                node.available = _supported(node, nodes)
            nodes[node.node_id] = node
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=self.scope_id,
                    node_id=node.node_id,
                    kind=node.kind,
                    artifact_id=_artifact_id(node),
                    available=node.available,
                    payload=node.model_dump_json().encode(),
                )
            )
            await _sync_memory(memory, (node,))

    async def _validate_reference(self, connection: AsyncConnection, node: _Node, memory: MemoryService) -> None:
        repositories = self.application.contexts.repositories
        try:
            if node.kind == "source" and isinstance(node.reference, SourceRef):
                await repositories.sources.get(connection, self.scope_id, node.reference)
                return
            if node.kind == "memory" and isinstance(node.reference, MemoryCitation):
                await memory.validate_citation(node.reference)
                return
            if node.kind == "topic" and isinstance(node.reference, ArtifactRef):
                await repositories.artifacts.get(connection, self.scope_id, node.reference)
                return
        except Exception as error:
            raise SourceLifecycleError("registered reference must be readable in this scope") from error  # noqa: TRY003
        raise SourceLifecycleError("reference does not match node kind")  # noqa: TRY003

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        runtime, contexts = self.application.runtime, self.application.contexts
        async with (
            runtime._scope_operation(self.scope_id),
            runtime._locked(self.scope_id),
            contexts.database.transaction() as connection,
        ):
            nodes = await self._load(connection)
            root = nodes.get(event.node_id)
            if root is None or root.kind != "source" or root.revision != event.revision:
                raise SourceLifecycleError("unknown source or lifecycle revision mismatch")  # noqa: TRY003 - Public validation error detail.
            if root.event is not None and (
                event.sequence < root.event.sequence or (event.sequence == root.event.sequence and event != root.event)
            ):
                raise SourceLifecycleError("event sequence is stale or conflicting")  # noqa: TRY003 - Public validation error detail.
            root.available, root.event = event.available, event
            _propagate(nodes)
            services = contexts._services_for(self.scope_id)
            _, catalog = services.sources(connection)
            await _sync_memory(services.memory(catalog, connection), tuple(nodes.values()))
            for node in nodes.values():
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(
                        SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                    )
                    .values(available=node.available, payload=node.model_dump_json().encode())
                )
        return sorted(node.node_id for node in nodes.values() if node.available)

    async def resolve(self, node_id: str, *, purpose: str = "generation") -> SourceRef | MemoryCitation | ArtifactRef:
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown lifecycle purpose")  # noqa: TRY003 - Public validation error detail.
        async with (
            self.application.runtime._scope_operation(self.scope_id),
            self.application.contexts.database.transaction() as connection,
        ):
            nodes = await self._load(connection)
        node = nodes.get(node_id)
        if node is None or (purpose == "generation" and not node.available):
            raise SourceLifecycleError("unknown or unavailable lifecycle node")  # noqa: TRY003 - Public validation error detail.
        return node.reference

    async def _load(self, connection: AsyncConnection) -> dict[str, _Node]:
        table = SOURCE_LIFECYCLE_NODES_TABLE
        rows = await connection.scalars(select(table.c.payload).where(table.c.scope_id == self.scope_id))
        nodes = [_Node.model_validate_json(row) for row in rows]
        return {node.node_id: node for node in nodes}


def _artifact_id(node: _Node) -> str | None:
    if isinstance(node.reference, ArtifactRef):
        return node.reference.artifact_id
    if isinstance(node.reference, MemoryCitation):
        return node.reference.memory_ref.artifact_id
    return None


def _supported(node: _Node, nodes: dict[str, _Node]) -> bool:
    states = [nodes[parent].available for parent in node.parents]
    return all(states) if node.policy == "all" else any(states)


def _propagate(nodes: dict[str, _Node]) -> None:
    complete = {node_id for node_id, node in nodes.items() if node.kind == "source"}
    while len(complete) < len(nodes):
        ready = [node for node in nodes.values() if node.node_id not in complete and set(node.parents) <= complete]
        if not ready:
            raise SourceLifecycleError("cyclic lifecycle graph")  # noqa: TRY003 - Public validation error detail.
        for node in ready:
            node.available = _supported(node, nodes)
            complete.add(node.node_id)


async def _sync_memory(memory: MemoryService, nodes: tuple[_Node, ...]) -> None:
    grouped: dict[str, list[_Node]] = {}
    for node in nodes:
        if node.kind == "memory" and isinstance(node.reference, MemoryCitation):
            grouped.setdefault(node.reference.memory_ref.artifact_id, []).append(node)
    for artifact_id, members in grouped.items():
        current = await memory.head(artifact_id)
        entries = {entry.entry_id: entry for entry in await memory.entries(current)}
        states = {entry.entry_id: entry.state for entry in current.content.manifest.entries}
        for available in (False, True):
            selected = []
            for node in members:
                reference = node.reference
                if not isinstance(reference, MemoryCitation):
                    continue
                entry = entries.get(reference.entry_id)
                if entry is None or entry.entry_version_id != reference.entry_version_id:
                    raise SourceLifecycleError("registered Memory body version changed")  # noqa: TRY003 - Public validation error detail.
                if node.available == available and (states[entry.entry_id] == "active") != available:
                    selected.append(entry)
            if selected:
                operation = memory.reactivate if available else memory.forget
                current = await operation(current, entries=tuple(selected), reason="source lifecycle")
