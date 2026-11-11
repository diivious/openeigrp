#!/usr/bin/env python3
# Copyright (C) 2026 Donnie V. Savage
"""Run a test command, stream diagnostics and retain a concurrency-safe result."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "logs"
GROUPS = {"uut/eigrp", "uut/scale", "frr", "linux", "netlab"}


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("group", choices=sorted(GROUPS))
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("test command is required")

    parent = RESULTS / args.group
    parent.mkdir(parents=True, exist_ok=True)
    # mkdtemp provides exclusive creation even for concurrent CI/test invocations.
    run_dir = Path(tempfile.mkdtemp(
        prefix=datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-"),
        dir=parent))
    log_path = run_dir / "output.log"
    status = 1
    error = None
    try:
        with log_path.open("w", encoding="utf-8", errors="replace") as log:
            try:
                proc = subprocess.Popen(command, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True,
                                        errors="replace")
                assert proc.stdout is not None
                for line in proc.stdout:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                    log.write(line)
                status = proc.wait()
            except (OSError, KeyboardInterrupt) as exc:
                error = str(exc)
                if isinstance(exc, KeyboardInterrupt):
                    if "proc" in locals() and proc.poll() is None:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                            proc.wait()
                    status = 130
                else:
                    print(f"test-log: {exc}", file=sys.stderr)
                    status = 1
    finally:
        result = {"command": command, "cwd": os.getcwd(), "returncode": status,
                  "result": "PASS" if status == 0 else "FAIL",
                  "timestamp": datetime.now(timezone.utc).isoformat(),
                  "error": error}
        # Exclusive directory plus atomic rename avoids partial structured reports.
        pending = run_dir / "result.json.tmp"
        pending.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        pending.replace(run_dir / "result.json")
        print(f"Test evidence: {log_path}", flush=True)
    return status


if __name__ == "__main__":
    sys.exit(run())
