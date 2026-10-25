#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise real process-limit decay and lockout with 90s monotonic uptime."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
UUT = ROOT / "eigrp/test/uut"
SCENARIO = UUT / "prefix-limit/scenario-process-dampened-ipv4.yaml"

# YAML may wait 15s for the initial adjacency, 15s for the learned route,
# six times 10s for process state, and 20s for adjacency recovery. The outer
# watchdog must leave time for commands, startup and cleanup as well.
SCENARIO_TIMEOUT = 150

# This interposer affects only the subprocess test environment. It simulates
# a newly booted Linux CI runner without advancing the actual machine clock.
CLOCK_SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <time.h>

int clock_gettime(clockid_t id, struct timespec *ts)
{
    static int (*next_clock)(clockid_t, struct timespec *);
    static uint64_t start;
    uint64_t now, fake;
    int result;
    if (!next_clock)
        next_clock = dlsym(RTLD_NEXT, "clock_gettime");
    result = next_clock(id, ts);
    if (result || id != CLOCK_MONOTONIC)
        return result;
    now = (uint64_t)ts->tv_sec * 1000U + (uint64_t)ts->tv_nsec / 1000000U;
    if (!start)
        start = now;
    fake = 90000U + now - start;
    ts->tv_sec = (time_t)(fake / 1000U);
    ts->tv_nsec = (long)((fake % 1000U) * 1000000U);
    return 0;
}
'''


def run_scenario(command, env, timeout):
    try:
        return subprocess.run(command, cwd=ROOT, env=env, text=True,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        # The verbose runner prints each step before executing it. Preserve
        # that progress when the outer watchdog fires on a busy CI runner.
        def decode(output):
            if isinstance(output, bytes):
                return output.decode(errors="replace")
            return output or ""

        pytest.fail(f"UUT exceeded {timeout}s; last progress:\n"
                    f"stdout:\n{decode(exc.stdout)}\n"
                    f"stderr:\n{decode(exc.stderr)}")


def test_scenario_timeout_covers_assertion_budgets():
    scenario = yaml.safe_load(SCENARIO.read_text())
    wait_budget = sum(float(arg.get("timeout", 10)) for step in scenario["steps"]
                      for op, arg in step.items()
                      if op in ("validate-neighbor", "validate-route", "validate-process-limit"))
    assert SCENARIO_TIMEOUT >= wait_budget + 30


def test_scenario_timeout_reports_last_step(monkeypatch):
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 70,
                                        output=b"-- validate-process-limit\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", expire)
    with pytest.raises(pytest.fail.Exception, match="validate-process-limit"):
        run_scenario(["uut"], {}, 70)


def test_process_dampening_fresh_linux_uptime(tmp_path):
    if sys.platform != "linux":
        pytest.skip("clock interposition test is Linux-specific; YAML UUT runs on macOS")
    shim = tmp_path / "monotime.c"
    library = tmp_path / "monotime.so"
    shim.write_text(CLOCK_SHIM)
    built = subprocess.run([os.environ.get("CC", "cc"), "-shared", "-fPIC",
                            "-o", str(library), str(shim), "-ldl"],
                           text=True, capture_output=True)
    assert built.returncode == 0, built.stdout + built.stderr
    built = subprocess.run(["make", "-C", str(UUT), "eigrp-uut-node"],
                           text=True, capture_output=True)
    assert built.returncode == 0, built.stdout + built.stderr
    env = os.environ.copy()
    env["LD_PRELOAD"] = str(library)
    ran = run_scenario([sys.executable, str(UUT / "run.py"),
                        str(UUT / "examples/two-router.yaml"), str(SCENARIO),
                        "--verbose"], env, SCENARIO_TIMEOUT)
    assert ran.returncode == 0, ran.stdout + ran.stderr


def test_penalty_bounds_include_async_replay_elapsed():
    """A replay delay must not turn legitimate further decay into a failure."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("eigrp_uut_run", UUT / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)

    aged = 90000
    replay = 5000
    actual = int(65536 + 65536 * 2 ** (-(aged + replay) / 450000))
    min_penalty, max_penalty = runner.process_penalty_bounds(aged, replay)
    assert min_penalty <= actual <= max_penalty
    assert actual < runner.process_penalty_bounds(aged, 0)[0]
    # A full half-life retains the original, narrow Q16 decay oracle.
    assert runner.process_penalty_bounds(450000, 0) == (98048, 98560)
    # Incorrect penalty accumulation remains outside the replay-time bounds.
    assert not min_penalty <= 65536 <= max_penalty
