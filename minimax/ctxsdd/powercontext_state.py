"""Actual PowerContext Source journal persistence, not an application-owned imitation.

Each operation opens the same PowerContext SQLite database in a *new process*.
The Source catalog stores immutable progress observations with stable provenance;
subsequent context preparation reads and folds those captured events.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .core import ROOT, snapshot


def _settings():
    try:
        from powercontext.builtin.persistence.sqlite import SQLiteConfig
        from powercontext.builtin.runtime import BuiltinConfig
    except ImportError as exc:
        raise RuntimeError("PowerContext SDK unavailable: install 'powercontext[builtin]>=1.2,<2'") from exc
    database = Path(os.environ.get("MM_PC_DB", str(ROOT / "records" / "powercontext.sqlite3"))).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    scope = os.environ.get("MM_PC_SCOPE", "minimax-sdd-reading-list-task")
    return BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database.as_posix()}")), scope


async def _scope(contexts, scope_key: str):
    from powercontext.builtin.scope import ScopeDraft

    # Stable idempotency key makes a fresh process reopen the SAME project Scope.
    scope = await contexts.scopes.create(
        ScopeDraft(title="MiniMax SDD reading-list task", summary="SDD task progress and verification", idempotency_key=scope_key)
    )
    return await contexts.get(scope.scope_id), scope.scope_id


def _project_events(entries):
    events = []
    for entry in sorted(entries, key=lambda x: x.position):
        source = entry.source
        if not getattr(source, "name", "").startswith("mm-sdd-task-"):
            continue
        try:
            payload = json.loads(source.content)
        except (ValueError, TypeError, AttributeError):
            continue
        if payload.get("schema") == "minimax.powercontext-event.v1":
            events.append({"position": entry.position, "source_id": source.name, **payload})
    return events


def _reduce(events):
    result = {"event_count": len(events), "last_stage": None, "last_verification": None, "todo": [], "last_context_sha256": None}
    for event in events:
        result["last_stage"] = event.get("stage")
        if event.get("context_sha256"):
            result["last_context_sha256"] = event["context_sha256"]
        if event.get("stage") == "verified":
            result["last_verification"] = event.get("verification")
            result["todo"] = [] if event.get("verification", {}).get("passed") else ["Fix test failures and rerun"]
        elif event.get("stage") == "patch_generated":
            result["todo"] = ["Review/apply model patch", "Run feature and regression tests"]
        elif event.get("stage") == "patch_applied":
            result["todo"] = ["Run feature and regression tests"]
    if result["last_verification"] is not None:
        prior = result["last_verification"].get("snapshot", {})
        changed = sorted(path for path, sha in prior.items() if snapshot().get(path) != sha)
        result["last_verification"] = {**result["last_verification"], "fresh": not changed and bool(result["last_verification"].get("passed")), "invalidated_paths": changed}
        if changed:
            result["todo"] = ["Inputs changed: rerun verification"]
    return result


async def _record(stage: str, **details):
    from powercontext.builtin.runtime import open_builtin_contexts
    from powercontext.builtin.sources import ContentCapture

    config, key = _settings()
    payload = {"schema": "minimax.powercontext-event.v1", "stage": stage, "at": datetime.now(UTC).isoformat(), **details}
    source_id = "mm-sdd-task-" + uuid.uuid4().hex
    async with open_builtin_contexts(config) as contexts:
        scoped, scope_id = await _scope(contexts, key)
        source, position = await scoped.sources.capture(ContentCapture(source_id=source_id, content=json.dumps(payload, ensure_ascii=False)))
        records = _project_events(await scoped.sources.journal.entries())
        state = _reduce(records)
        return {"provider": "powercontext", "scope_id": scope_id, "source_id": source.name,
                "journal_position": position, "state": state}


async def _read():
    from powercontext.builtin.runtime import open_builtin_contexts

    config, key = _settings()
    async with open_builtin_contexts(config) as contexts:
        scoped, scope_id = await _scope(contexts, key)
        events = _project_events(await scoped.sources.journal.entries())
        return {"provider": "powercontext", "scope_id": scope_id, "events": events, "state": _reduce(events)}


def record(stage: str, **details: Any):
    if stage not in {"context_built", "patch_generated", "patch_applied", "verified"}:
        raise ValueError("unknown task stage")
    return asyncio.run(_record(stage, **details))


def read():
    return asyncio.run(_read())
