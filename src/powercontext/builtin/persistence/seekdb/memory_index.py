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

"""Single-statement native seekdb hybrid candidate retrieval."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import CapabilityNotSupportedError, MemorySearchChannels, MemorySearchRequest
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_FTS = """
SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id, v.text,
       'fts' AS channel, MATCH(m.searchable_text) AGAINST (:query) AS score, NULL AS distance
FROM pc_memory_entry_heads AS m
JOIN pc_memory_entry_versions AS v
 ON v.scope_id = m.scope_id AND v.memory_artifact_id = m.memory_artifact_id
 AND v.entry_version_id = m.entry_version_id
WHERE m.scope_id = :scope_id AND m.memory_artifact_id IN :memory_artifact_ids
 AND MATCH(m.searchable_text) AGAINST (:query)
{tag_filter}
ORDER BY score DESC, m.memory_artifact_id, m.entry_id, m.entry_version_id
LIMIT :candidate_limit
"""
_VECTOR = """
SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id, v.text,
       'vector' AS channel, 0 AS score, l2_distance(m.embedding, :query_vector) AS distance
FROM pc_memory_vector_entries AS m
JOIN pc_memory_entry_versions AS v
 ON v.scope_id = m.scope_id AND v.memory_artifact_id = m.memory_artifact_id
 AND v.entry_version_id = m.entry_version_id
WHERE m.scope_id = :scope_id AND m.memory_artifact_id IN :memory_artifact_ids
{tag_filter}
ORDER BY l2_distance(m.embedding, :query_vector){approximate}
LIMIT :candidate_limit
"""


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Keep per-channel limits and indexes inside one SQL, then use ordinary fusion."""

    @override
    async def search(
        self, connection: AsyncConnection, scope_id: str, request: MemorySearchRequest, /
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        profile = self.capabilities.embedding_profile
        if profile is None or request.embedding_profile != profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self.vector_complete(connection, scope_id, request.memories, profile):
            raise CapabilityNotSupportedError("vector")
        tagged = request.tag_filter is not None
        filters = memory_tag_sql("m") if tagged else ""
        fts = _FTS.format(tag_filter=filters)
        vector = _VECTOR.format(tag_filter=filters, approximate="" if tagged else " APPROXIMATE")
        # Derived tables preserve each channel's top-k before the outer ordering.
        statement = text(
            f"SELECT * FROM (SELECT * FROM ({fts}) AS fts_candidates "  # noqa: S608 - Only fixed SQL fragments; values are bound.
            f"UNION ALL SELECT * FROM ({vector}) AS vector_candidates) AS candidates "
            "ORDER BY channel, score DESC, distance, memory_artifact_id, entry_id, entry_version_id"
        ).bindparams(
            bindparam("memory_artifact_ids", expanding=True), bindparam("query_vector", type_=VECTOR(profile.dimension))
        )
        if tagged:
            statement = statement.bindparams(
                bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True)
            )
        rows = (
            (
                await connection.execute(
                    statement,
                    {
                        "scope_id": scope_id,
                        "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                        "query": request.analyzed_query,
                        "query_vector": request.query_vector,
                        "candidate_limit": request.candidate_limit,
                        **memory_tag_parameters(request.tag_filter),
                    },
                )
            )
            .mappings()
            .all()
        )
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in rows if row["channel"] == "fts"), request.memories),
            vector=memory_channel_hits((row for row in rows if row["channel"] == "vector"), request.memories),
        )
