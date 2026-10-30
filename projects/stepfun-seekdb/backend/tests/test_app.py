import math
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Chunk, SearchPayload, fuse_candidates, vector_sql  # noqa: E402


def row(key):
    return {"id": key, "source_id": "source-123", "title": key, "content": "text"}


def test_fusion_merges_both_channels_and_preserves_order():
    results = fuse_candidates([row("a"), row("b")], [row("b"), row("c")], 3)
    assert [result["id"] for result in results] == ["b", "a", "c"]
    assert results[0]["matched_by"] == ["vector", "fts"]


def test_fusion_is_deterministic():
    args = ([row("x"), row("y")], [row("y"), row("x")], 2)
    assert fuse_candidates(*args) == fuse_candidates(*args)


def test_reject_nonfinite_embeddings():
    with pytest.raises(ValidationError):
        Chunk(
            id="chunk-123", source_id="source-123", title="Test",
            content="Hello", model_id="Xenova/paraphrase-multilingual-MiniLM-L12-v2",
            vector=[math.nan] + [0.1] * 383,
        )
    with pytest.raises(ValidationError):
        SearchPayload(query="hello", vector=[0.1] * 3)


def test_vector_sql_format_is_a_vector_literal():
    vector = [0.125] * 384
    literal = vector_sql(vector)
    assert literal.startswith("[") and literal.endswith("]")
    assert len(literal[1:-1].split(",")) == 384
