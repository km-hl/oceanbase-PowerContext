"""Offline tests for context freshness, storage and fail-closed model invocation."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ctxsdd import core
from ctxsdd.__main__ import ask_minimax, apply_patch


class ContextSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        sample = Path(__file__).resolve().parents[1]
        shutil.copytree(sample / ".sdd", self.root / ".sdd")
        shutil.copytree(sample / "business", self.root / "business",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.rebase = patch.object(core, "ROOT", self.root)
        self.rebase.start()
        self.addCleanup(self.rebase.stop)
        self.backend = patch.dict(os.environ, {"MM_OB_HOST": ""})
        self.backend.start()
        self.addCleanup(self.backend.stop)

    def test_only_latest_active_revision_is_retrieved(self):
        active, inactive = core.current_specs()
        self.assertEqual([(s["id"], s["revision"]) for s in active], [("reading-list-filter", 2)])
        self.assertEqual(inactive, [])
        self.assertTrue(all(item["kind"] in {"spec", "code", "test"} for item in core.documents()))

    def test_index_search_uses_persisted_rows(self):
        rows = core.documents()
        with core.Store() as store:
            self.assertEqual(store.replace(rows), len(rows))
            hits = store.select("status min_rating", 12)
            self.assertTrue(any(row["kind"] == "spec" for row in hits))
            self.assertTrue(any(row["kind"] == "code" for row in hits))
            self.assertGreater(len(hits), 0)

    def test_verification_becomes_stale_when_spec_changes_without_version_bump(self):
        original = core.snapshot()
        with core.Store() as store:
            store.event("verification", {"passed": True, "snapshot": original, "checks": {}})
        with core.Store() as store:
            self.assertTrue(core.latest_verification(store.events())["fresh"])
        path = self.root / ".sdd/specs/reading-list-r2.json"
        spec = json.loads(path.read_text())
        spec["requirements"].append("新增加的规格约束")
        path.write_text(json.dumps(spec, ensure_ascii=False))
        with core.Store() as store:
            result = core.latest_verification(store.events())
            self.assertFalse(result["fresh"])
            self.assertIn(".sdd/specs/reading-list-r2.json", result["invalidated_paths"])

    def test_deleted_source_invalidates_past_test(self):
        with core.Store() as store:
            store.event("verification", {"passed": True, "snapshot": core.snapshot(), "checks": {}})
        (self.root / "business/reading_list/service.py").unlink()
        with core.Store() as store:
            result = core.latest_verification(store.events())
            self.assertFalse(result["fresh"])

    def test_unsafe_paths_rejected(self):
        for path in ("../escape.py", "/root/private", "records/secrets", "business/../../secret"):
            with self.assertRaises(ValueError):
                core.safe(path)

    def test_model_requires_real_api_key(self):
        with patch.dict(os.environ, {"MM_MINIMAX_API_KEY": ""}):
            with self.assertRaisesRegex(RuntimeError, "no MiniMax request has been sent"):
                ask_minimax("add filters")

    def test_patch_rejects_unrelated_files(self):
        path = self.root / "patch.txt"
        path.write_text("diff --git a/README.md b/README.md\n@@ -1 +1 @@\n-old\n+new\n")
        with self.assertRaisesRegex(ValueError, "outside the authorized"):
            apply_patch(path)


if __name__ == "__main__":
    unittest.main()
