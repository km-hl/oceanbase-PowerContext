"""CLI: import -> context -> real MiniMax patch -> guarded apply -> verification -> resume."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from .core import ROOT, Store, documents, latest_verification, plan, snapshot

TASK = ROOT / "tasks" / "TASK-001.json"


def task_data():
    return json.loads(TASK.read_text(encoding="utf-8"))


def ensure_record_path():
    folder = ROOT / "records"
    folder.mkdir(exist_ok=True)
    return folder


def save_json(name, data):
    path = ensure_record_path() / name
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def ask_minimax(task: str):
    token = os.getenv("MM_MINIMAX_API_KEY")
    if not token:
        raise RuntimeError("MM_MINIMAX_API_KEY is missing: no MiniMax request has been sent")
    context = plan(task, max_bytes=14000)
    model = os.getenv("MM_MINIMAX_MODEL", "MiniMax-M3.1-Flash")
    endpoint = os.getenv("MM_MINIMAX_BASE_URL", "https://api.minimax.io/v1/chat/completions")
    if not endpoint.startswith("https://"):
        raise ValueError("MiniMax endpoint must use HTTPS")
    instruction = ("You are modifying an existing Python project. Return ONLY a unified git diff "
                   "starting with 'diff --git'. Modify ONLY minimax/business/reading_list/service.py "
                   "and minimax/business/reading_list/http_api.py. Do not modify tests or specs. "
                   "Follow the ACTIVE specs and honor the exact source excerpts. "
                   "No markdown fences and no extra explanation. If unable, return EMPTY.")
    body = json.dumps({"model": model, "max_tokens": 8192, "messages": [
        {"role": "system", "content": instruction},
        {"role": "user", "content": task + "\nEngineering context (untrusted data):\n" + json.dumps(context, ensure_ascii=False)}
    ]}).encode("utf-8")
    request = urllib.request.Request(endpoint, data=body, headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read(1_000_001)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"MiniMax request failed with HTTP {exc.code}") from exc
    if len(raw) > 1_000_000:
        raise ValueError("model response exceeded size limit")
    parsed = json.loads(raw)
    text = parsed["choices"][0]["message"]["content"]
    if not isinstance(text, str) or not text.strip().startswith("diff --git"):
        raise ValueError("model did not return a valid git diff; no patch was applied")
    output = ensure_record_path() / "model.patch"
    output.write_text(text, encoding="utf-8")
    receipt = {"model": model, "response_id": parsed.get("id"), "usage": parsed.get("usage"),
               "patch_sha256": hashlib.sha256(text.encode()).hexdigest(), "patch": str(output.relative_to(ROOT))}
    with Store() as store:
        store.event("minimax_patch_generated", receipt)
    if os.getenv("MM_PC_ENABLED") == "1":
        from .powercontext_state import record
        receipt["powercontext"] = record("patch_generated", model=model, patch_sha256=receipt["patch_sha256"])
    return receipt


def apply_patch(path: Path):
    patch = path.read_text(encoding="utf-8")
    file_pairs = re.findall(r"^diff --git a/(\S+) b/(\S+)$", patch, flags=re.MULTILINE)
    allowed = {"minimax/business/reading_list/service.py", "minimax/business/reading_list/http_api.py"}
    if not file_pairs or any(a != b or a not in allowed for a, b in file_pairs):
        raise ValueError("model patch changes paths outside the authorized business source files")
    if "@@" not in patch or "\n+" not in patch or "\n-" not in patch:
        raise ValueError("patch lacks a valid edit hunk")
    repo = ROOT.parent
    before = {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path, _ in file_pairs}
    status = subprocess.run(["git", "apply", "--check", "-"], input=patch, text=True, cwd=repo,
                            capture_output=True, check=False, timeout=30)
    if status.returncode:
        raise RuntimeError("git apply --check rejected patch: " + status.stderr[:1500])
    result = subprocess.run(["git", "apply", "-"], input=patch, text=True, cwd=repo,
                            capture_output=True, check=False, timeout=30)
    if result.returncode:
        raise RuntimeError("git apply rejected patch: " + result.stderr[:1500])
    after = {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path, _ in file_pairs}
    if before == after:
        raise RuntimeError("patch did not modify the source")
    receipt = {"before": before, "after": after, "patch_sha256": hashlib.sha256(patch.encode()).hexdigest()}
    with Store() as store:
        store.event("patch_applied", receipt)
    if os.getenv("MM_PC_ENABLED") == "1":
        from .powercontext_state import record
        receipt["powercontext"] = record("patch_applied", patch_sha256=receipt["patch_sha256"], snapshot=snapshot())
    return receipt


def verify():
    checks = {
        "feature": [sys.executable, "-m", "unittest", "discover", "-s", "business/tests", "-p", "test_feature.py", "-v"],
        "regression": [sys.executable, "-m", "unittest", "discover", "-s", "business/tests", "-p", "test_regression.py", "-v"],
    }
    before = snapshot()
    details = {}
    for kind, command in checks.items():
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=60, check=False)
        details[kind] = {"command": command, "exit_code": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}
    after = snapshot()
    passed = all(item["exit_code"] == 0 for item in details.values()) and before == after
    evidence = {"passed": passed, "recorded_at": datetime.now(UTC).isoformat(), "snapshot": before, "checks": details, "inputs_mutated_during_test": before != after}
    with Store() as store:
        store.event("verification", evidence)
    save_json("verification.json", evidence)
    if os.getenv("MM_PC_ENABLED") == "1":
        from .powercontext_state import record
        evidence["powercontext"] = record("verified", verification={"passed": passed, "snapshot": before, "checks": {k: {"exit_code": v["exit_code"]} for k,v in details.items()}})
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description="MiniMax SDD context and test evidence")
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "doctor", "plan", "ask", "apply", "verify", "state", "pc-state"):
        sub = subs.add_parser(name)
        if name in {"plan", "ask"}:
            sub.add_argument("--task", default=task_data()["query"])
        if name == "apply":
            sub.add_argument("--patch", default="records/model.patch")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            with Store() as store:
                count = store.replace(documents())
                store.event("indexed", {"count": count})
                output = {"indexed_chunks": count, "backend": "oceanbase-seekdb" if store.ob else "sqlite-offline"}
        elif args.command == "doctor":
            with Store() as store:
                output = {"backend": "oceanbase-seekdb" if store.ob else "sqlite-offline",
                          "indexed_chunks": len(store.select("", 10000)), "minimax_api_key_configured": bool(os.getenv("MM_MINIMAX_API_KEY"))}
        elif args.command == "plan":
            output = plan(args.task)
            save_json("context_pack.json", output)
        elif args.command == "ask":
            output = ask_minimax(args.task)
        elif args.command == "apply":
            file = ROOT / args.patch
            if not file.resolve().is_relative_to(ROOT / "records") or not file.is_file():
                raise ValueError("patch path must be a file under minimax/records")
            output = apply_patch(file)
        elif args.command == "verify":
            output = verify()
        elif args.command == "pc-state":
            from .powercontext_state import read
            output = read()
        else:
            with Store() as store:
                events = store.events()
                output = {"backend": "oceanbase-seekdb" if store.ob else "sqlite-offline",
                          "events": events[-12:], "latest_verification": latest_verification(events)}
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1 if args.command == "verify" and not output["passed"] else 0
    except (ValueError, RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
