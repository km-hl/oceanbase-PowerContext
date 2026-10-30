# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
"""Agent-friendly, offline SDD context and verification command-line interface."""

from __future__ import annotations

import argparse
import json
import sys

from powercontext.builtin.sdd.engine import SDDContext, SDDError


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare authoritative SDD context for the current worktree")
    parser.add_argument("--repo", default=".", help="Local project root")
    commands = parser.add_subparsers(dest="operation", required=True)
    context = commands.add_parser("context", help="Read effective specs, code, tests and verification freshness")
    context.add_argument("--task", required=True)
    context.add_argument("--path", action="append", default=[])
    context.add_argument("--max-bytes", type=int, default=16384)
    verify = commands.add_parser("run-test", help="Execute a test and save its input fingerprint")
    verify.add_argument("--id", required=True)
    verify.add_argument("--file", action="append", required=True)
    verify.add_argument("--spec", action="append", required=True)
    verify.add_argument("--timeout", type=int, default=120)
    verify.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        service = SDDContext(args.repo)
        if args.operation == "context":
            result = service.prepare(args.task, paths=tuple(args.path), max_bytes=args.max_bytes)
        else:
            command = args.command[1:] if args.command and args.command[0] == "--" else args.command
            result = service.run_test(args.id, command, files=tuple(args.file),
                                      specs=tuple(args.spec), timeout=args.timeout)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.operation == "context" or result["passed"] else 1
    except (SDDError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
