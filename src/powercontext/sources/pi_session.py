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

"""Lossless Pi v3 branch evidence and durable incremental acquisition."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, JsonValue

from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _records(value: PiSessionInput) -> tuple[str, dict[str, dict[str, JsonValue]]]:
    try:
        records = [json.loads(line) for line in value.content.decode("utf-8-sig").splitlines() if line.strip()]
        if not records or not isinstance(records[0], dict):
            raise ValueError("missing Pi session header")  # noqa: TRY003
        header = records[0]
        if header.get("type") != "session" or type(header.get("version")) is not int or header["version"] != 3:
            raise ValueError("expected Pi session version 3")  # noqa: TRY003
        session_id = header.get("id")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("missing session identity")  # noqa: TRY003
        nodes: dict[str, dict[str, JsonValue]] = {}
        for record in records[1:]:
            event_id = _validate_node(record, nodes)
            nodes[event_id] = record
        if value.leaf_id is not None and value.leaf_id not in nodes:
            raise ValueError("unknown Pi leaf")  # noqa: TRY003
    except (UnicodeError, TypeError) as error:
        raise ValueError("invalid Pi JSONL") from error  # noqa: TRY003
    return session_id, nodes


def _validate_node(record: object, nodes: dict[str, dict[str, JsonValue]]) -> str:
    if not isinstance(record, dict):
        raise ValueError("Pi nodes must be JSON objects")  # noqa: TRY003, TRY004 - Archive validation contract.
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id.strip() or event_id in nodes:
        raise ValueError("Pi event identities must be unique nonempty strings")  # noqa: TRY003
    if "parentId" not in record:
        raise ValueError("missing parentId")  # noqa: TRY003
    parent = cast(dict[str, JsonValue], record)["parentId"]
    if parent is not None and (not isinstance(parent, str) or parent not in nodes):
        raise ValueError("Pi parent must precede its child")  # noqa: TRY003
    _canonical(record)
    return event_id


def _visible_blocks(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if block.get("type") == "text" and isinstance(text, str):
            parts.append(text)
        elif block.get("type") == "toolCall":
            parts.append(
                f"[tool_call {block.get('name', '')} {block.get('id', '')}] {_canonical(block.get('arguments', {}))}"
            )
    return "\n".join(parts)


def _event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_type = record.get("type")
    body = ""
    role = ""
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str):
            raise ValueError("invalid Pi message")  # noqa: TRY003
        role = message["role"]
        body = _visible_blocks(message.get("content"))
        if role == "toolResult":
            if body:
                body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {body}"
        elif role == "bashExecution":
            command, output = message.get("command", ""), message.get("output", "")
            body = f"$ {command}\n{output}" if command or output else ""
    elif event_type in ("compaction", "branch_summary"):
        role = event_type
        body = record.get("summary", "")
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_blocks(record.get("content"))
    if not isinstance(body, str) or not body:
        return None
    return PiEventInput.model_validate({
        "session_id": session_id,
        "event_id": record["id"],
        "parent_id": record["parentId"],
        "event_type": event_type,
        "role": role,
        "content": f"{role}: {body}",
        "record": record,
    })


def _branch(value: PiSessionInput, session_id: str, nodes: dict[str, dict[str, JsonValue]]) -> tuple[PiEventInput, ...]:
    leaf = value.leaf_id if value.leaf_id is not None else next(reversed(nodes), None)
    chain = []
    while leaf is not None:
        record = nodes[leaf]
        event = _event(session_id, record)
        if event is not None:
            chain.append(event)
        leaf = cast(str | None, record["parentId"])
    return tuple(reversed(chain))


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse the entire graph, then select original evidence on one ancestry path."""
    session_id, nodes = _records(value)
    return _branch(value, session_id, nodes)


def _identity(session_id: str, event_id: str) -> str:
    return "pi:" + hashlib.sha256(_canonical([session_id, event_id]).encode()).hexdigest()


class _PiEventSource(Source, PiEventInput):
    pass


class _PiAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = _PiEventSource

    async def resolve(self, value: PiEventInput, /) -> _PiEventSource:
        if not value.session_id.strip() or not value.event_id.strip():
            raise ValueError("Pi identities must not be blank")  # noqa: TRY003
        return _PiEventSource(
            name=_identity(value.session_id, value.event_id),
            materialization=SourceMaterialization.CAPTURED,
            **value.model_dump(),
        )

    async def read(self, source: _PiEventSource, /) -> PiEventInput:
        return PiEventInput.model_validate(source.model_dump())


class _PiProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = _PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                key: getattr(source, key) for key in ("session_id", "event_id", "parent_id", "event_type", "role")
            },
        )


PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition[PiEventInput, _PiEventSource, PiEventInput](
    _PiAdapter(), projections=(_PiProjection(),)
)


class PiSessionConnector:
    """Submit only unseen events; lifecycle commits progress after durable ingestion."""

    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        value = PiSessionInput(content=await asyncio.to_thread(self.path.read_bytes), leaf_id=self.leaf_id)
        session_id, nodes = _records(value)
        events = _branch(value, session_id, nodes)
        previous = session.checkpoint
        if previous is not None and (
            not isinstance(previous, dict)
            or previous.get("session_id") != session_id
            or not isinstance(previous.get("imported"), dict)
        ):
            raise ValueError("checkpoint does not match this Pi session")  # noqa: TRY003
        imported = dict(previous["imported"]) if previous is not None else {}
        # Check all previously imported branches before writing any new event.
        for event_id, digest in imported.items():
            if event_id not in nodes or hashlib.sha256(_canonical(nodes[event_id]).encode()).hexdigest() != digest:
                raise ValueError("previously imported Pi record was changed")  # noqa: TRY003
        for event in events:
            if event.event_id not in imported:
                await session.submit(_identity(session_id, event.event_id), "pi-event", event)
                imported[event.event_id] = hashlib.sha256(_canonical(event.record).encode()).hexdigest()
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE, checkpoint={"session_id": session_id, "imported": imported}
        )
