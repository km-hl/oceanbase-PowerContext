# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
"""A real source edit makes a formerly passing verification stale."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from powercontext.builtin.sdd import SDDContext, SDDError


class SDDContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "src").mkdir()
        (self.root / "tests").mkdir()
        (self.root / ".sdd" / "specs").mkdir(parents=True)
        (self.root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (self.root / "tests" / "test_calc.py").write_text(
            "import sys\nsys.path.insert(0, 'src')\nfrom calc import add\nassert add(2, 3) == 5\n",
            encoding="utf-8",
        )
        self.spec("arithmetic", 1, title="Arithmetic API", requirements=["add two numbers"],
                  paths=["src/*.py"], tests=["tests/test_calc.py"], decisions=["Return a numeric sum"])
        self.engine = SDDContext(self.root)

    def spec(self, name: str, revision: int, *, title="Arithmetic API", requirements=None,
             paths=None, tests=None, decisions=None, status="active", supersedes=None) -> None:
        payload = {"id": name, "revision": revision, "title": title,
                   "status": status, "paths": paths or ["src/*.py"],
                   "tests": tests or ["tests/test_calc.py"],
                   "requirements": requirements or ["add numbers"],
                   "decisions": decisions or [], "supersedes": supersedes or []}
        (self.root / ".sdd" / "specs" / f"{name}-r{revision}.json").write_text(json.dumps(payload), encoding="utf-8")

    def context(self):
        return self.engine.prepare("update Arithmetic API", paths=("src/calc.py",))

    def test_context_includes_spec_current_code_tests_and_validated_proof(self):
        baseline = self.context()
        self.assertEqual(baseline["specifications"][0]["id"], "arithmetic")
        self.assertEqual(baseline["code"][0]["path"], "src/calc.py")
        self.assertEqual(baseline["tests"][0]["path"], "tests/test_calc.py")
        result = self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                                      files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))
        self.assertTrue(result["passed"])
        checked = self.context()
        self.assertEqual(checked["verifications"][0]["status"], "valid")
        self.assertEqual(checked["verifications"][0]["reasons"], [])

    def test_real_code_edit_invalidates_proof_and_failed_rerun(self):
        self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                             files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))
        (self.root / "src" / "calc.py").write_text("def add(a, b):\n    return a - b  # deliberately broken\n", encoding="utf-8")
        obsolete = self.context()["verifications"][0]
        self.assertEqual(obsolete["status"], "stale_or_failed")
        self.assertIn("file_changed:src/calc.py", obsolete["reasons"])
        rerun = self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                                     files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))
        self.assertFalse(rerun["passed"])
        self.assertIn("test_failed", self.context()["verifications"][0]["reasons"])
        # Correct the actual code and execute again; only then may it be marked valid.
        (self.root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        self.assertTrue(self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                        files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))["passed"])
        self.assertEqual(self.context()["verifications"][0]["status"], "valid")

    def test_spec_revision_change_invalidates_prior_verification(self):
        self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                             files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))
        self.spec("arithmetic", 2, decisions=["Use integer addition with no implicit string conversion"])
        view = self.context()
        self.assertEqual([s["revision"] for s in view["specifications"]], [2])
        self.assertEqual(view["inactive_specs"][0]["reason"], "older_revision")
        self.assertIn("spec_changed:arithmetic", view["verifications"][0]["reasons"])

    def test_same_revision_spec_edit_invalidates_verified_evidence(self):
        self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                             files=("src/calc.py", "tests/test_calc.py"), specs=("arithmetic",))
        self.spec("arithmetic", 1, decisions=["New requirement without incrementing revision"])
        result = self.context()["verifications"][0]
        self.assertEqual(result["status"], "stale_or_failed")
        self.assertIn("spec_changed:arithmetic", result["reasons"])

    def test_superseded_spec_is_never_presented_as_current(self):
        self.spec("modern-arithmetic", 1, title="Arithmetic API replacement",
                  decisions=["New decision"], supersedes=["arithmetic"])
        view = self.context()
        self.assertEqual([s["id"] for s in view["specifications"]], ["modern-arithmetic"])
        self.assertEqual(view["inactive_specs"][0]["reason"], "superseded_by_new_spec")
        self.assertNotIn("Return a numeric sum", str(view["specifications"]))

    def test_retired_spec_cannot_be_used_for_new_verification(self):
        self.spec("arithmetic", 2, status="retired")
        self.assertEqual(self.context()["specifications"], [])
        with self.assertRaises(SDDError):
            self.engine.run_test("arith", [sys.executable, "tests/test_calc.py"],
                                 files=("src/calc.py",), specs=("arithmetic",))

    def test_path_safety_and_no_symlink_reads(self):
        with self.assertRaises(SDDError):
            self.engine.prepare("update", paths=("../private.txt",))
        with self.assertRaises(SDDError):
            self.engine.prepare("update", paths=(".env",))
        outside = self.root.parent / f"outside-{os.getpid()}.txt"
        outside.write_text("secret", encoding="utf-8")
        self.addCleanup(outside.unlink)
        (self.root / "src" / "symlink.py").symlink_to(outside)
        self.assertNotIn("src/symlink.py", [x["path"] for x in self.context()["code"]])

    def test_budget_respected_and_no_match_is_explicit(self):
        (self.root / "src" / "calc.py").write_text(("a = " + "x" * 200 + "\n") * 500, encoding="utf-8")
        view = self.engine.prepare("update Arithmetic API", paths=("src/calc.py",), max_bytes=2048)
        self.assertLessEqual(len(json.dumps(view, ensure_ascii=False, separators=(",", ":")).encode()), 2048)
        self.assertTrue(view["truncated"])
        empty = self.engine.prepare("unrelated-quantum-thermodynamics")
        self.assertEqual(empty["specifications"], [])

    def test_command_line_uses_real_project_and_emits_json(self):
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")}
        result = subprocess.run([sys.executable, "-m", "powercontext.builtin.sdd", "--repo", str(self.root),
                                 "context", "--task", "Arithmetic API", "--path", "src/calc.py"],
                                check=False, capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["specifications"][0]["id"], "arithmetic")


if __name__ == "__main__":
    unittest.main()
