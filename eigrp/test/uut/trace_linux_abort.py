#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""On CI failure, capture native Linux stacks for the three IPv6 peer-limit UUTs.

Rebuild the exact portable UUT implementation with debug symbols and frame
pointers, retain -O2 and the runner's default libc hardening, and preload a
SIGABRT backtrace hook into the C nodes only. Original YAML logs are untouched.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
OUT = HERE / "abort-trace"
SCENARIOS = (
    "prefix-limit/scenario-neighbor-enforce-ipv6.yaml",
    "prefix-limit-lifecycle/scenario-peer-ipv6-suspend-release.yaml",
    "prefix-limit-lifecycle/scenario-peer-timers-ipv6.yaml",
)
CFLAGS = (
    "-O2 -g -std=c11 -D_POSIX_C_SOURCE=200809L -pthread "
    "-Wall -Wextra -Wno-unused-parameter -Wno-unused-const-variable "
    "-rdynamic -fno-omit-frame-pointer -fno-optimize-sibling-calls"
)


def run(command: list[str], *, env=None, timeout=180):
    return subprocess.run(command, cwd=ROOT, env=env, text=True,
                          capture_output=True, timeout=timeout)


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    compiler = os.environ.get("CC", "cc")
    trace = OUT / "abort_trace.so"
    built = run([compiler, "-shared", "-fPIC", "-g", "-o", str(trace),
                 str(HERE / "abort_trace.c")], timeout=30)
    if built.returncode:
        raise RuntimeError(f"trace hook compile failed:\n{built.stderr}")
    # Do not `make clean`: it would delete the primary failed YAML logs.
    (HERE / "eigrp-uut-node").unlink(missing_ok=True)
    compiled = run(["make", "-C", "eigrp/test/uut", "eigrp-uut-node",
                    f"CFLAGS={CFLAGS}"], timeout=180)
    (OUT / "rebuild.log").write_text(compiled.stdout + compiled.stderr)
    if compiled.returncode:
        raise RuntimeError("UUT diagnostic rebuild failed: see rebuild.log")
    # The actual default compiler configuration, including FORTIFY, matters.
    macros = subprocess.run([compiler, "-dM", "-E", "-O2", "-x", "c", "-"],
                            input="", cwd=ROOT, capture_output=True, text=True)
    macros_text = "\n".join(line for line in macros.stdout.splitlines()
                             if "FORTIFY" in line or "SSP" in line)
    version = run([compiler, "--version"], timeout=10)
    glibc = run(["ldd", "--version"], timeout=10)
    revision = run(["git", "rev-parse", "HEAD"], timeout=10)
    linkage = run(["readelf", "-Ws", str(HERE / "eigrp-uut-node")],
                  timeout=10)
    checked = "\n".join(line for line in linkage.stdout.splitlines()
                        if "_chk" in line or "fortify" in line.lower())
    (OUT / "toolchain.txt").write_text(
        f"revision: {revision.stdout.strip()}\ncompiler: {compiler}\n"
        f"{version.stdout}\nglibc: {glibc.stdout.splitlines()[:1]}\n"
        f"CFLAGS: {CFLAGS}\n"
        f"default predefines:\n{macros_text or '(none)'}\n"
        f"linked fortified symbols:\n{checked or '(none)'}\n"
    )
    return trace


def main():
    if sys.platform != "linux":
        print("Linux-only diagnostic; skipped")
        return 0
    trace = prepare()
    env = os.environ.copy()
    env["EIGRP_UUT_NODE_LD_PRELOAD"] = str(trace)
    failures = 0
    for relative in SCENARIOS:
        scenario = HERE / relative
        # The scenario already declares the canonical topology. Let the
        # existing runner validate and locate it rather than duplicating it.
        topology_name = yaml.safe_load(scenario.read_text())["topology"]
        topology = (scenario.parent / topology_name).resolve()
        label = scenario.stem
        command = [sys.executable, str(HERE / "run.py"), str(topology),
                   str(scenario), "--verbose", "--report"]
        try:
            result = run(command, env=env, timeout=165)
            content = result.stdout + result.stderr
            status = result.returncode
        except subprocess.TimeoutExpired as exc:
            content = "Scenario timed out after 165s\n" + str(exc)
            status = 124
        (OUT / f"{label}.log").write_text(content)
        print(f"{relative}: exit={status}", flush=True)
        if "=== UUT SIGABRT native backtrace" in content:
            # Backtraces also appear inside the runner's collected node stderr
            # on failures. Full original output remains in per-case log files.
            part = content.split("=== UUT SIGABRT native backtrace", 1)[1]
            print("=== UUT SIGABRT native backtrace" + part[:9000], flush=True)
        elif status:
            print(content[-2500:], flush=True)
        if status:
            failures += 1
    print(f"Diagnostic runs failed: {failures}/{len(SCENARIOS)}; "
          f"logs: {OUT.relative_to(ROOT)}")
    # This is a diagnostic follow-up to an already failed job, not a gate.
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"Trace setup failed: {error}", file=sys.stderr)
        raise SystemExit(1)
