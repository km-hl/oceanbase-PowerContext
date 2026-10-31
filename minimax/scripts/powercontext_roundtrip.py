"""PowerContext SDK integration check using NEW Python processes and a clean DB."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(env: dict[str, str], *args: str):
    command = [sys.executable, "-m", "ctxsdd", *args]
    proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180, check=False)
    if proc.returncode:
        raise RuntimeError(f"{command!r} exit={proc.returncode}: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


def main():
    with tempfile.TemporaryDirectory(prefix="minimax-pc-") as directory:
        env = dict(os.environ)
        env.pop("MM_OB_HOST", None)
        env["MM_PC_ENABLED"] = "1"
        env["MM_PC_DB"] = str(Path(directory) / "powercontext.sqlite3")
        env["MM_PC_SCOPE"] = "sdd-roundtrip-" + Path(directory).name
        run(env, "init")
        initial = run(env, "plan", "--task", "status min_rating")
        assert initial["progress"]["event_count"] == 0, initial["progress"]
        verification = run(env, "verify")
        assert verification["passed"] and verification["powercontext"]["provider"] == "powercontext"
        recovered = run(env, "pc-state")
        assert recovered["provider"] == "powercontext"
        assert recovered["state"]["event_count"] >= 2
        assert recovered["state"]["last_verification"]["fresh"]
        resumed = run(env, "plan", "--task", "status min_rating")
        assert resumed["progress"]["last_verification"]["fresh"]
        assert resumed["progress"]["last_stage"] == "verified"
        print(json.dumps({
            "sdk": "powercontext.builtin.runtime.open_builtin_contexts",
            "storage": "PowerContext Source journal via SQLiteConfig",
            "scope_id": recovered["scope_id"],
            "journal_events": recovered["state"]["event_count"],
            "restarted_process_reused_verification": True,
            "recovered_stage": resumed["progress"]["last_stage"],
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
