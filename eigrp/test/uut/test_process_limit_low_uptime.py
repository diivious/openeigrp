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
SCENARIO_TIMEOUT = 175

# This interposer affects only the subprocess test environment. It simulates
# a newly booted Linux CI runner without advancing the actual machine clock.
CLOCK_SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdint.h>
#include <time.h>

/* CLOCK_MONOTONIC is rebased for the test, but Linux condition-variable
 * timed waits use the kernel's unre-based monotonic clock.  Translate only
 * deadlines in the synthetic clock domain back to real absolute time. */
static _Atomic uint64_t start_msec;
static int (*next_clock)(clockid_t, struct timespec *);

int clock_gettime(clockid_t id, struct timespec *ts)
{
    uint64_t start, expected, now, fake;
    int result;
    if (!next_clock)
        next_clock = dlsym(RTLD_NEXT, "clock_gettime");
    result = next_clock(id, ts);
    if (result || id != CLOCK_MONOTONIC)
        return result;
    now = (uint64_t)ts->tv_sec * 1000U + (uint64_t)ts->tv_nsec / 1000000U;
    start = atomic_load(&start_msec);
    if (!start) {
        expected = 0;
        atomic_compare_exchange_strong(&start_msec, &expected, now);
        start = atomic_load(&start_msec);
    }
    fake = 90000U + now - start;
    ts->tv_sec = (time_t)(fake / 1000U);
    ts->tv_nsec = (long)((fake % 1000U) * 1000000U);
    return 0;
}

int pthread_cond_timedwait(pthread_cond_t *cond, pthread_mutex_t *mutex,
                           const struct timespec *abstime)
{
    static int (*next_wait)(pthread_cond_t *, pthread_mutex_t *,
                            const struct timespec *);
    struct timespec actual_now, deadline;
    uint64_t start, real_now, fake_now, fake_due;
    int64_t real_due;

    if (!next_wait)
        next_wait = dlsym(RTLD_NEXT, "pthread_cond_timedwait");
    start = atomic_load(&start_msec);
    if (!abstime || !start || !next_clock ||
        next_clock(CLOCK_MONOTONIC, &actual_now) != 0)
        return next_wait(cond, mutex, abstime);

    real_now = (uint64_t)actual_now.tv_sec * 1000U +
               (uint64_t)actual_now.tv_nsec / 1000000U;
    fake_now = 90000U + real_now - start;
    fake_due = (uint64_t)abstime->tv_sec * 1000U +
               (uint64_t)abstime->tv_nsec / 1000000U;
    /* Ignore CLOCK_REALTIME deadlines and any unmodified monotonic waits.
     * The UUT's monotonic timers are at most minutes into the future. */
    if (fake_due < fake_now || fake_due - fake_now > 600000U)
        return next_wait(cond, mutex, abstime);

    real_due = (int64_t)fake_due + (int64_t)start - 90000;
    if (real_due < 0)
        return next_wait(cond, mutex, abstime);
    deadline.tv_sec = (time_t)(real_due / 1000);
    deadline.tv_nsec = (long)((real_due % 1000) * 1000000);
    return next_wait(cond, mutex, &deadline);
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
    waits = sum(float(arg if isinstance(arg, (int, float)) else arg.get("seconds", 1))
                for step in scenario["steps"] for op, arg in step.items()
                if op == "wait")
    assert SCENARIO_TIMEOUT >= wait_budget + waits + 30


def test_scenario_timeout_reports_last_step(monkeypatch):
    def expire(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 70,
                                        output=b"-- validate-process-limit\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", expire)
    with pytest.raises(pytest.fail.Exception, match="validate-process-limit"):
        run_scenario(["uut"], {}, 70)


def test_clock_preload_is_injected_only_into_uut_nodes(monkeypatch):
    """The runner must retain real time while its C children get the shim."""
    import importlib.util
    import io

    spec = importlib.util.spec_from_file_location("eigrp_uut_run", UUT / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.setenv("EIGRP_UUT_NODE_LD_PRELOAD", "/tmp/monotime.so")
    monkeypatch.setenv("LD_PRELOAD", "/tmp/other.so")
    captured = {}

    class FakeProcess:
        stderr = io.StringIO("")

        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    node = runner.Node("r1", "/tmp/r1.conf", "/tmp/uut.sock")
    monkeypatch.setattr(node, "_start_stdout_reader", lambda: None)
    monkeypatch.setattr(node, "_readline", lambda _: "READY")
    node.start()
    assert captured["env"]["LD_PRELOAD"] == "/tmp/monotime.so /tmp/other.so"
    assert "EIGRP_UUT_NODE_LD_PRELOAD" not in captured["env"]
    assert os.environ["LD_PRELOAD"] == "/tmp/other.so"


def test_clock_shim_preserves_monotonic_condition_wait(tmp_path):
    """Synthetic uptime must not make pthread's real-clock wait spin."""
    if sys.platform != "linux":
        pytest.skip("clock interposition is Linux-specific")
    shim = tmp_path / "monotime.c"
    library = tmp_path / "monotime.so"
    probe = tmp_path / "deadline.c"
    binary = tmp_path / "deadline"
    shim.write_text(CLOCK_SHIM)
    probe.write_text(r"""
#define _GNU_SOURCE
#include <errno.h>
#include <pthread.h>
#include <time.h>

int main(void)
{
    pthread_condattr_t attr;
    pthread_cond_t cond;
    pthread_mutex_t mutex = PTHREAD_MUTEX_INITIALIZER;
    struct timespec deadline, before, after;
    long elapsed;
    int result;

    if (pthread_condattr_init(&attr) ||
        pthread_condattr_setclock(&attr, CLOCK_MONOTONIC) ||
        pthread_cond_init(&cond, &attr) ||
        clock_gettime(CLOCK_MONOTONIC, &deadline) ||
        clock_gettime(CLOCK_MONOTONIC_RAW, &before))
        return 1;
    deadline.tv_nsec += 200000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }
    pthread_mutex_lock(&mutex);
    result = pthread_cond_timedwait(&cond, &mutex, &deadline);
    pthread_mutex_unlock(&mutex);
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &after))
        return 2;
    elapsed = (after.tv_sec - before.tv_sec) * 1000L +
              (after.tv_nsec - before.tv_nsec) / 1000000L;
    pthread_cond_destroy(&cond);
    pthread_condattr_destroy(&attr);
    pthread_mutex_destroy(&mutex);
    return result == ETIMEDOUT && elapsed >= 150 && elapsed < 3000 ? 0 : 3;
}
""")
    compiler = os.environ.get("CC", "cc")
    for command in ([compiler, "-shared", "-fPIC", "-o", str(library),
                     str(shim), "-ldl"],
                    [compiler, "-o", str(binary), str(probe), "-pthread"]):
        built = subprocess.run(command, text=True, capture_output=True)
        assert built.returncode == 0, built.stdout + built.stderr
    env = os.environ.copy()
    env["LD_PRELOAD"] = str(library)
    ran = subprocess.run([str(binary)], env=env, capture_output=True,
                         text=True, timeout=5)
    assert ran.returncode == 0, ran.stdout + ran.stderr


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
    env["EIGRP_UUT_NODE_LD_PRELOAD"] = str(library)
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
