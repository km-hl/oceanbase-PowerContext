"""Actual seekdb SQL import/retrieval verification (requires running DB)."""
from __future__ import annotations

import json
import os

from ctxsdd.core import Store, documents, plan


def main():
    if not os.getenv("MM_OB_HOST"):
        raise RuntimeError("MM_OB_HOST must point to a live OceanBase or seekdb server")
    with Store() as store:
        assert store.ob, "SQLite is not sufficient for seekdb verification"
        rows = documents()
        indexed = store.replace(rows)
        with store._sql("SELECT VERSION(), @@version_comment") as cursor:
            version, comment = cursor.fetchone()
        count = store._sql("SELECT COUNT(*) FROM mm_documents").fetchone()[0]
        counts = store._sql("SELECT kind, COUNT(*) FROM mm_documents GROUP BY kind").fetchall()
        assert count == indexed and count > 0
    prepared = plan("status min_rating")
    assert prepared["backend"] == "oceanbase-seekdb"
    kinds = {row["kind"] for row in prepared["evidence"]}
    assert kinds == {"spec", "code", "test"}, kinds
    assert prepared["specifications"][0]["id"] == "reading-list-filter"
    assert any(x["path"].endswith("service.py") for x in prepared["evidence"])
    print(json.dumps({
        "database_version": version,
        "version_comment": comment,
        "backend": prepared["backend"],
        "persisted_documents": count,
        "kinds_and_counts": counts,
        "retrieved_context_items": len(prepared["evidence"]),
        "example_sql": "SELECT kind,path,line_from,line_to,symbol,sha256,content FROM mm_documents",
        "context_spec_sha256": prepared["specifications"][0]["sha256"],
        "actual_sql_retrieval": True,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
