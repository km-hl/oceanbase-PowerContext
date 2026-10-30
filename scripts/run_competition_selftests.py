"""Run committed public tests with original bytes, including on Windows checkouts."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

TASKS = ("csv", "pi", "pdf", "rerank", "incremental", "count-trigger", "embedding", "lifecycle", "evidence", "hybrid")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Build the locked official self-test image first")
    parser.add_argument("--task", action="append", choices=TASKS, help="Run selected tasks; default: all")
    options = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    docker, git = shutil.which("docker"), shutil.which("git")
    if docker is None or git is None:
        parser.error("Docker and Git must be installed")
    environment = {**os.environ, "POWERCONTEXT_CANDIDATE": str(root)}
    compose = [docker, "compose", "-f", str(root / "competition/compose.yaml")]
    if options.build:
        subprocess.run([*compose, "build", "selftest"], cwd=root, env=environment, check=True)  # noqa: S603 - Fixed Docker arguments.
    cache = root / ".pytest_cache"
    cache.mkdir(exist_ok=True)
    # git show returns committed bytes, unaffected by core.autocrlf. In particular,
    # session fixture digests must not be calculated over Windows-translated data.
    files_output = subprocess.check_output([git, "ls-tree", "-rz", "--name-only", "HEAD", "competition"], cwd=root)  # noqa: S603 - Fixed Git arguments.
    files = files_output.decode("utf-8").strip("\0").split("\0")
    with tempfile.TemporaryDirectory(prefix="competition-", dir=cache) as directory:
        exported = Path(directory).resolve()
        if not exported.is_relative_to(cache.resolve()):
            raise ValueError("self-test export must remain inside the workspace cache")  # noqa: TRY003
        for filename in files:
            target = (exported / Path(filename).relative_to("competition")).resolve()
            if not target.is_relative_to(exported):
                raise ValueError("invalid competition asset path")  # noqa: TRY003
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output([git, "show", f"HEAD:{filename}"], cwd=root))  # noqa: S603 - Git object path, no shell.
        targets = " ".join(f"test-{task}" for task in options.task) if options.task else "test"
        command = (
            "sed 's/\\r$//' /usr/local/bin/selftest-entrypoint > /tmp/selftest-entrypoint; "
            "bash /tmp/selftest-entrypoint make -k -C /selftest "
            f"{targets} PYTHON=/opt/selftest-venv/bin/python PRODUCT=/candidate"
        )
        return subprocess.run(  # noqa: S603 - Fixed command; targets are argparse choices.
            [
                *compose,
                "run",
                "--rm",
                "--volume",
                f"{exported}:/selftest:ro",
                "--entrypoint",
                "/bin/bash",
                "selftest",
                "-c",
                command,
            ],
            cwd=root,
            env=environment,
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
