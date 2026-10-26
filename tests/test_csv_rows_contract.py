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

"""Additional CSV contract cases, independent of the competition fixtures."""

import asyncio
import csv
import io
import json

import pytest


def _parse(content: bytes, *, document_id: str = "independent", primary_key: str = "id"):
    from powercontext.sources.csv_rows import CSVDocumentInput, parse_csv_document

    return parse_csv_document(CSVDocumentInput(document_id=document_id, primary_key=primary_key, content=content))


def test_csv_row_roundtrip_preserves_json_and_csv_sensitive_strings():
    from powercontext.sources import SourceDefinitionRegistry, TextEvidence
    from powercontext.sources.csv_rows import CSV_ROW_SOURCE_DEFINITION
    from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY

    columns = ("id", "名称", "note", "empty", "number", "boolean")
    values = (" key ", "重庆,机场", 'first\r\nsecond "quoted" line', "", "000123", "false")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    writer.writerow(values)
    (row,) = _parse(buffer.getvalue().encode("utf-8"))
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    source = asyncio.run(registry.resolve(row))
    assert source.name == source.source_id == "csv:independent:%20key%20"
    assert asyncio.run(registry.read(source)) == row
    evidence = TextEvidence.model_validate(registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY))
    assert list(json.loads(evidence.content).items()) == list(zip(columns, values, strict=True))
    assert evidence.content == json.dumps(
        dict(zip(columns, values, strict=True)), ensure_ascii=False, separators=(",", ":")
    )


@pytest.mark.parametrize(
    "content",
    [
        b"\n",
        b"\xef\xbb\xbf",
        b"\nid,name\n1,one\n",
        b"id,\t\n1,a\n",
        b'id,name\n1,"ok"extra\n',
        b"id,name\n1,a\n2,b,extra\n",
        b"id,name\n1,a\n ,blank\n",
    ],
)
def test_invalid_document_is_rejected_atomically(content):
    with pytest.raises(ValueError):
        _parse(content)


@pytest.mark.parametrize("document_id", ["", " ", "\tdoc", "doc\n"])
def test_invalid_document_id_rejected_even_for_header_only_csv(document_id):
    with pytest.raises(ValueError):
        _parse(b"id,name\n", document_id=document_id)


def test_blank_physical_lines_do_not_drop_quoted_empty_cells():
    assert len(_parse(b'id,name\n\n1,\n\n2,""\r\n')) == 2
    with pytest.raises(ValueError):
        _parse(b'id\n""\n')


def test_header_and_primary_keys_are_not_trimmed():
    first, second = _parse(b" id ,id,name\n A ,1,one\nA,2,two\n", primary_key=" id ")
    assert first.columns == (" id ", "id", "name")
    assert first.primary_key_value == " A "
    assert second.primary_key_value == "A"
    with pytest.raises(ValueError):
        _parse(b" id ,name\n1,one\n", primary_key="id")


def test_source_id_limit_is_applied_after_percent_encoding():
    from powercontext.sources.csv_rows import csv_row_source_id

    assert len(csv_row_source_id("d", "x" * 250)) == 256
    with pytest.raises(ValueError):
        csv_row_source_id("d", "x" * 251)
    with pytest.raises(ValueError):
        _parse(("id\n" + "中" * 29 + "\n").encode())


@pytest.mark.parametrize("primary_key", ["", "\t", "absent"])
def test_missing_or_blank_primary_key_is_rejected(primary_key):
    with pytest.raises(ValueError):
        _parse(b"id,name\n1,one\n", primary_key=primary_key)


@pytest.mark.parametrize(
    ("columns", "values", "primary_key", "primary_key_value"),
    [
        (("id", "id"), ("1", "2"), "id", "1"),
        (("id", "name"), ("1",), "id", "1"),
        (("id", "name"), ("1", "one"), "missing", "1"),
        (("id", "name"), ("1", "one"), "id", "different"),
        (("id", " "), ("1", "one"), "id", "1"),
    ],
)
def test_registry_rejects_inconsistent_direct_row_inputs(columns, values, primary_key, primary_key_value):
    from powercontext.sources import SourceDefinitionRegistry
    from powercontext.sources.csv_rows import CSV_ROW_SOURCE_DEFINITION, CSVRowInput

    value = CSVRowInput(
        document_id="direct",
        primary_key=primary_key,
        primary_key_value=primary_key_value,
        columns=columns,
        values=values,
    )
    registry = SourceDefinitionRegistry((CSV_ROW_SOURCE_DEFINITION,))
    with pytest.raises(ValueError):
        asyncio.run(registry.resolve(value))


def test_identity_encoding_has_no_delimiter_or_percent_collision():
    from powercontext.sources.csv_rows import csv_row_source_id

    assert csv_row_source_id("a:b", "c/d") == "csv:a%3Ab:c%2Fd"
    assert csv_row_source_id("a:b", "c/d") != csv_row_source_id("a", "b:c/d")
    assert csv_row_source_id("a", "%2F") != csv_row_source_id("a", "/")
