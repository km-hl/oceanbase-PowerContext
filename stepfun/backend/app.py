"""StepFun Knowledge Lab: browser embeddings + real seekdb hybrid retrieval."""
from __future__ import annotations

import math
import os
import time
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

import pymysql
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

MODEL_ID = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"
DIMENSIONS = 384
SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    source_id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    content TEXT NOT NULL,
    model_id VARCHAR(128) NOT NULL,
    embedding VECTOR(384) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_source (source_id),
    FULLTEXT INDEX idx_content (content) WITH PARSER ik,
    VECTOR INDEX idx_embedding (embedding)
        WITH (DISTANCE=l2, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP
"""


@contextmanager
def connection():
    db = pymysql.connect(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", "2881")),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        database=os.getenv("SEEKDB_DATABASE", "stepfun_kb"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=4,
        read_timeout=30,
        write_timeout=30,
        autocommit=False,
    )
    try:
        yield db
    finally:
        db.close()


def initialize_database():
    """Wait for the actual seekdb instance; never silently switch engines."""
    for attempt in range(35):
        try:
            with connection() as db:
                with db.cursor() as cursor:
                    cursor.execute(SCHEMA)
                db.commit()
            return
        except pymysql.MySQLError:
            if attempt == 34:
                raise
            time.sleep(2)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    await asyncio.to_thread(initialize_database)
    yield


app = FastAPI(title="StepFun Knowledge Lab", lifespan=lifespan)


class Chunk(BaseModel):
    id: str = Field(min_length=8, max_length=64)
    source_id: str = Field(min_length=8, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=2000)
    model_id: str = Field(min_length=1, max_length=128)
    vector: list[float] = Field(min_length=DIMENSIONS, max_length=DIMENSIONS)

    @field_validator("vector")
    @classmethod
    def finite_vector(cls, value: list[float]) -> list[float]:
        if not all(math.isfinite(x) for x in value):
            raise ValueError("Embeddings must be finite")
        if not any(x != 0 for x in value):
            raise ValueError("Zero embeddings are not supported")
        return value


class IngestPayload(BaseModel):
    chunks: list[Chunk] = Field(min_length=1, max_length=32)


class SearchPayload(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    vector: list[float] | None = None
    mode: Literal["hybrid", "vector", "text"] = "hybrid"
    top_k: int = Field(default=8, ge=1, le=20)
    model_id: str = MODEL_ID

    @field_validator("vector")
    @classmethod
    def valid_query_vector(cls, value: list[float] | None) -> list[float] | None:
        if value is not None and (len(value) != DIMENSIONS or not all(math.isfinite(x) for x in value)):
            raise ValueError("Query embedding must contain 384 finite numbers")
        return value


def vector_sql(values: list[float]) -> str:
    """Serialize a validated client-generated embedding for seekdb's VECTOR type."""
    return "[" + ",".join(format(v, ".9g") for v in values) + "]"


def fuse_candidates(vector_rows: list[dict], text_rows: list[dict], limit: int) -> list[dict]:
    """Reciprocal rank fusion (RRF); deterministic ties and explicit channel provenance."""
    hits: dict[str, dict] = {}
    for channel, rows in (("vector", vector_rows), ("fts", text_rows)):
        for rank, row in enumerate(rows, start=1):
            identity = row["id"]
            if identity not in hits:
                hits[identity] = {
                    "id": identity,
                    "source_id": row["source_id"],
                    "title": row["title"],
                    "content": row["content"],
                    "score": 0.0,
                    "matched_by": [],
                }
            hit = hits[identity]
            hit["score"] += 1.0 / (60 + rank)
            hit["matched_by"].append(channel)
    return sorted(hits.values(), key=lambda item: (-item["score"], item["id"]))[:limit]


@app.get("/api/health")
def health():
    try:
        with connection() as db:
            with db.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) AS chunks FROM knowledge_chunks")
                count = cursor.fetchone()["chunks"]
        return {"ok": True, "engine": "seekdb", "chunks": count, "model_id": MODEL_ID}
    except pymysql.MySQLError as exc:
        raise HTTPException(status_code=503, detail="seekdb is not ready") from exc


@app.get("/api/documents")
def documents():
    with connection() as db:
        with db.cursor() as cursor:
            cursor.execute("""
                SELECT source_id, MAX(title) AS title, COUNT(*) AS chunks,
                       MAX(created_at) AS created_at
                FROM knowledge_chunks GROUP BY source_id ORDER BY created_at DESC LIMIT 100
            """)
            rows = cursor.fetchall()
    for row in rows:
        row["created_at"] = str(row["created_at"])
    return {"documents": rows}


@app.delete("/api/documents/{source_id}")
def delete_document(source_id: str):
    if len(source_id) > 64 or len(source_id) < 8:
        raise HTTPException(status_code=422, detail="Invalid source ID")
    with connection() as db:
        with db.cursor() as cursor:
            cursor.execute("DELETE FROM knowledge_chunks WHERE source_id = %s", (source_id,))
            deleted = cursor.rowcount
        db.commit()
    return {"deleted_chunks": deleted}


@app.post("/api/ingest")
def ingest(payload: IngestPayload):
    for chunk in payload.chunks:
        if chunk.model_id != MODEL_ID:
            raise HTTPException(status_code=422, detail="Incorrect embedding model")
    with connection() as db:
        try:
            with db.cursor() as cursor:
                for chunk in payload.chunks:
                    cursor.execute("""
                        INSERT INTO knowledge_chunks
                        (id, source_id, title, content, model_id, embedding)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        title=VALUES(title), content=VALUES(content),
                        model_id=VALUES(model_id), embedding=VALUES(embedding)
                    """, (
                        chunk.id, chunk.source_id, chunk.title, chunk.content,
                        chunk.model_id, vector_sql(chunk.vector),
                    ))
            db.commit()
        except Exception:
            db.rollback()
            raise
    return {"stored": len(payload.chunks), "engine": "seekdb"}


@app.post("/api/search")
def search(payload: SearchPayload):
    if payload.model_id != MODEL_ID:
        raise HTTPException(status_code=422, detail="Incorrect embedding model")
    if payload.mode != "text" and payload.vector is None:
        raise HTTPException(status_code=422, detail="Vector required for this mode")
    start = time.perf_counter()
    candidate_limit = min(80, max(24, payload.top_k * 4))
    vector_rows, text_rows = [], []
    with connection() as db:
        with db.cursor() as cursor:
            if payload.mode in ("hybrid", "vector"):
                cursor.execute("""
                    SELECT id, source_id, title, content,
                           l2_distance(embedding, %s) AS distance
                    FROM knowledge_chunks
                    WHERE model_id = %s
                    ORDER BY distance APPROXIMATE LIMIT %s
                """, (vector_sql(payload.vector), MODEL_ID, candidate_limit))
                vector_rows = cursor.fetchall()
            if payload.mode in ("hybrid", "text"):
                cursor.execute("""
                    SELECT id, source_id, title, content,
                           MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE) AS relevance
                    FROM knowledge_chunks
                    WHERE model_id = %s
                      AND MATCH(content) AGAINST (%s IN NATURAL LANGUAGE MODE)
                    ORDER BY relevance DESC, id ASC LIMIT %s
                """, (payload.query, MODEL_ID, payload.query, candidate_limit))
                text_rows = cursor.fetchall()
    results = fuse_candidates(vector_rows, text_rows, payload.top_k)
    return {
        "results": results,
        "candidate_counts": {"vector": len(vector_rows), "fts": len(text_rows)},
        "search_ms": round((time.perf_counter() - start) * 1000, 2),
        "engine": "seekdb",
        "fusion": "RRF(k=60)",
    }


STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True, check_dir=False), name="frontend")
