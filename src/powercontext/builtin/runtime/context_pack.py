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

"""Portable UTF-8 context packs and validated prefix deltas."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

PACK_SCHEMA = "powercontext.context-pack.v1"
DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """An invalid context wire representation or UTF-8 chunk boundary."""


def _json(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _validate_items(items: tuple[bytes, ...]) -> None:
    if not isinstance(items, tuple) or any(not isinstance(item, bytes) for item in items):
        raise ContextPackError("chunks must be a tuple of bytes")  # noqa: TRY003 - Public validation error detail.
    try:
        for item in items:
            item.decode("utf-8")
    except UnicodeError as error:
        raise ContextPackError("chunks must be valid UTF-8") from error  # noqa: TRY003 - Public validation error detail.


def _digest(items: tuple[bytes, ...]) -> str:
    return "sha256:" + hashlib.sha256(_json({"schema": PACK_SCHEMA, "items": _encode(items)})).hexdigest()


def _validate_digest(value: object) -> None:
    if not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ContextPackError("invalid digest")  # noqa: TRY003 - Public validation error detail.


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("duplicate wire field")  # noqa: TRY003 - Public validation error detail.
        result[key] = value
    return result


def _payload(value: bytes, schema: str, fields: set[str]) -> dict[str, Any]:
    try:
        payload = json.loads(value.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError, AttributeError) as error:
        raise ContextPackError("invalid wire JSON") from error  # noqa: TRY003 - Public validation error detail.
    if not isinstance(payload, dict) or set(payload) != fields | {"schema"} or payload["schema"] != schema:
        raise ContextPackError("invalid wire fields or schema")  # noqa: TRY003 - Public validation error detail.
    return payload


def _decode(value: object) -> tuple[bytes, ...]:
    if not isinstance(value, list):
        raise ContextPackError("chunks must be a Base64 string array")  # noqa: TRY003 - Public validation error detail.
    try:
        decoded = []
        for item in value:
            if not isinstance(item, str):
                raise ContextPackError("chunks must be Base64 strings")  # noqa: TRY003 - Public validation error detail.
            decoded.append(base64.b64decode(item, validate=True))
        items = tuple(decoded)
    except (ValueError, binascii.Error) as error:
        raise ContextPackError("invalid Base64") from error  # noqa: TRY003 - Public validation error detail.
    if _encode(items) != value:
        raise ContextPackError("noncanonical Base64")  # noqa: TRY003 - Public validation error detail.
    _validate_items(items)
    return items


@dataclass(frozen=True, slots=True)
class ContextPack:
    """A complete context whose digest includes the exact chunk boundaries."""

    items: tuple[bytes, ...]
    digest: str
    byte_count: int

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        _validate_items(items)
        digest = _digest(items)
        byte_count = 0
        while True:
            pack = cls(items, digest, byte_count)
            size = len(pack.to_bytes())
            if size == byte_count:
                return pack
            byte_count = size

    @classmethod
    def from_content(cls, content: str | None, *, chunk_bytes: int = 512) -> ContextPack:
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")  # noqa: TRY003 - Public validation error detail.
        if content is None:
            content = ""
        try:
            raw = content.encode("utf-8")
        except (UnicodeError, AttributeError) as error:
            raise ContextPackError("content must be UTF-8 text") from error  # noqa: TRY003 - Public validation error detail.
        chunks: list[bytes] = []
        start = 0
        while start < len(raw):
            end = min(start + chunk_bytes, len(raw))
            while end < len(raw) and end > start and raw[end] & 0xC0 == 0x80:
                end -= 1
            if end == start:
                raise ContextPackError("chunk limit cannot fit a UTF-8 character")  # noqa: TRY003 - Public validation error detail.
            chunks.append(raw[start:end])
            start = end
        return cls.build(tuple(chunks))

    def to_bytes(self) -> bytes:
        return _json({
            "schema": PACK_SCHEMA,
            "items": _encode(self.items),
            "digest": self.digest,
            "byte_count": self.byte_count,
        })

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPack:
        payload = _payload(value, PACK_SCHEMA, {"items", "digest", "byte_count"})
        pack = cls.build(_decode(payload["items"]))
        if (
            type(payload["byte_count"]) is not int
            or payload["digest"] != pack.digest
            or payload["byte_count"] != pack.byte_count
        ):
            raise ContextPackError("pack digest or byte count mismatch")  # noqa: TRY003 - Public validation error detail.
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """Reuse a base prefix and append a complete target suffix."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_digest(self.base_digest)
        _validate_digest(self.target_digest)
        if type(self.prefix_items) is not int or self.prefix_items < 0:
            raise ContextPackError("prefix_items must be a nonnegative integer")  # noqa: TRY003 - Public validation error detail.
        _validate_items(self.appended)

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        _validate_pack(base)
        _validate_pack(target)
        prefix = 0
        for left, right in zip(base.items, target.items, strict=False):
            if left != right:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        return _json({
            "schema": DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode(self.appended),
        })

    @classmethod
    def from_bytes(cls, value: bytes) -> ContextPackDelta:
        payload = _payload(value, DELTA_SCHEMA, {"base_digest", "target_digest", "prefix_items", "appended"})
        return cls(
            payload["base_digest"], payload["target_digest"], payload["prefix_items"], _decode(payload["appended"])
        )


def _validate_pack(pack: ContextPack) -> None:
    if not isinstance(pack, ContextPack) or pack != ContextPack.build(pack.items):
        raise ContextPackError("invalid context pack")  # noqa: TRY003 - Public validation error detail.


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    """Reconstruct independently, checking both base and target identities."""
    _validate_pack(base)
    if delta.base_digest != base.digest or delta.prefix_items > len(base.items):
        raise ContextPackError("base digest or prefix mismatch")  # noqa: TRY003 - Public validation error detail.
    target = ContextPack.build(base.items[: delta.prefix_items] + delta.appended)
    if target.digest != delta.target_digest:
        raise ContextPackError("target digest mismatch")  # noqa: TRY003 - Public validation error detail.
    return target
