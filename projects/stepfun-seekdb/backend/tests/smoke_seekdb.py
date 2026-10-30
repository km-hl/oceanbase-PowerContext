"""Optional real-service smoke test: python backend/tests/smoke_seekdb.py"""
import json
import os
import urllib.request
import uuid

BASE = os.getenv("STEPFUN_URL", "http://127.0.0.1:8000")
MODEL = "Xenova/paraphrase-multilingual-MiniLM-L12-v2"


def request(path, payload=None, method=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        BASE + "/api" + path, data=data,
        method=method or ("POST" if payload is not None else "GET"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=35) as response:
        return json.load(response)


def unit_vector(index):
    vector = [0.0] * 384
    vector[index] = 1.0
    return vector


def main():
    source_id = str(uuid.uuid4())
    chunks = [
        {
            "id": str(uuid.uuid4()), "source_id": source_id,
            "title": "Vector database notes", "content": "semantic vector retrieval with HNSW",
            "model_id": MODEL, "vector": unit_vector(0),
        },
        {
            "id": str(uuid.uuid4()), "source_id": source_id,
            "title": "Full text notes", "content": "keyword matching with inverted index",
            "model_id": MODEL, "vector": unit_vector(1),
        },
    ]
    try:
        assert request("/health")["engine"] == "seekdb"
        assert request("/ingest", {"chunks": chunks})["stored"] == 2
        result = request("/search", {
            "query": "semantic vector",
            "vector": unit_vector(0),
            "mode": "hybrid", "model_id": MODEL,
            "top_k": 2,
        })
        assert result["engine"] == "seekdb" and result["candidate_counts"]["vector"] >= 1
        assert any(hit["id"] == chunks[0]["id"] for hit in result["results"])
        assert request("/search", {
            "query": "keyword",
            "mode": "text", "model_id": MODEL, "top_k": 2,
        })["results"]
        print("PASS: seekdb ingest / vector / FTS / hybrid / source cleanup")
    finally:
        request("/documents/" + source_id, method="DELETE")


if __name__ == "__main__":
    main()
