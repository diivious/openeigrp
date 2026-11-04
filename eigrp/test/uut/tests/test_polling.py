#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Executable virtual-clock regression tests for scenario polling semantics."""
import importlib.util
from pathlib import Path

import pytest

UUT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eigrp_uut_timing_test", UUT / "run.py")
run = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run)


def runner_with(clock, node=None):
    runner = run.Runner({}, {}, Path("."), clock=clock)
    if node is not None:
        runner.nodes = {"r1": node}
    runner.verbose = False
    return runner


@pytest.mark.parametrize("delays", [(), (0.03, 0.07, 0.01, 0.09), (0.23, 0.04)])
def test_irregular_wakeups_do_not_change_stable_or_timeout_result(delays):
    # Virtual time includes controlled late wakeups, so CI load cannot alter
    # the result. Stable and permanently false predicates have fixed outcomes.
    class IrregularClock(run.ManualClock):
        index = 0
        def sleep(self, seconds):
            extra = delays[self.index % len(delays)] if delays else 0.0
            self.index += 1
            super().sleep(seconds + extra)
    clock = IrregularClock()
    assert run.Poller(clock).until(lambda: True, timeout=0.9, stable_for=0.5)
    assert 0.5 <= clock.elapsed <= 0.9
    clock = IrregularClock()
    assert not run.Poller(clock).until(lambda: False, timeout=0.5)
    assert clock.elapsed >= 0.5


def test_flapping_resets_stability_window_and_timeout_is_simulated():
    clock = run.ManualClock()
    outcomes = iter([True, True, False, True, True, True, True, True])
    assert not run.Poller(clock, 0.1).until(
        lambda: next(outcomes, False), timeout=0.6, stable_for=0.4)
    assert clock.elapsed == pytest.approx(0.6)


def test_late_blocking_snapshot_never_becomes_success():
    clock = run.ManualClock()
    def slow_state():
        clock.advance(0.31)
        return True
    assert not run.Poller(clock).until(slow_state, timeout=0.3)
    assert clock.elapsed == pytest.approx(0.31)


def test_exact_stability_and_deadline_boundaries_are_inclusive():
    clock = run.ManualClock()
    assert run.Poller(clock).until(lambda: True, timeout=0.2, stable_for=0.2)
    assert clock.elapsed == pytest.approx(0.2)
    clock = run.ManualClock()
    assert not run.Poller(clock).until(lambda: False, timeout=0.2)
    assert clock.elapsed == pytest.approx(0.2)


def test_observation_checks_boundary_and_rejects_late_failure():
    clock = run.ManualClock()
    def present():
        return clock.now() < 0.3
    assert not run.Poller(clock).throughout(present, 0.3)
    assert clock.elapsed == pytest.approx(0.3)
    clock = run.ManualClock()
    assert run.Poller(clock).throughout(lambda: True, 0.3)
    assert clock.elapsed == pytest.approx(0.3)


def test_state_snapshot_duration_is_not_credited_as_stability():
    clock = run.ManualClock()
    class Node:
        calls = 0
        def state(self):
            self.calls += 1
            clock.advance(0.14)
            return {"topology": [], "paths": [], "rib": [], "source": [],
                    "neighbors": [], "accounting": []}
    node = Node()
    runner = runner_with(clock, node)
    runner.expect_convergence({"timeout": 1, "stable_for": 0.3,
                               "expect": [{"route": {"uut": "r1", "prefix": "192.0.2.3/32", "present": False}}]})
    assert clock.elapsed >= 0.44
    assert node.calls >= 2


def test_convergence_no_late_pass_after_slow_snapshot():
    clock = run.ManualClock()
    class Node:
        def state(self):
            clock.advance(0.41)
            return {"topology": [], "paths": [], "rib": [], "source": [],
                    "neighbors": [], "accounting": []}
    runner = runner_with(clock, Node())
    with pytest.raises(AssertionError, match="timed out"):
        runner.expect_convergence({"timeout": 0.4, "stable_for": 0,
                                   "expect": [{"route": {"uut": "r1", "prefix": "192.0.2.3/32", "present": False}}]})


def test_all_basic_validator_polls_use_injected_clock():
    clock = run.ManualClock()
    class Node:
        count = 0
        stderr = []
        def state(self):
            self.count += 1
            return {"process_limit": {"state": 0}, "redist_limit": {"state": 0}}
    node = Node()
    runner = runner_with(clock, node)
    with pytest.raises(RuntimeError, match="redistribution limit state mismatch"):
        runner.step("validate-redist-limit", {"uut": "r1", "state": 1, "timeout": 0.2})
    assert clock.elapsed == pytest.approx(0.2)
    with pytest.raises(RuntimeError, match="process limit state mismatch"):
        runner.step("validate-process-limit", {"uut": "r1", "state": 1, "timeout": 0.2})
    assert clock.elapsed == pytest.approx(0.4)
    with pytest.raises(AssertionError, match="validate-log failed"):
        runner.step("validate-log", {"uut": "r1", "contains": "absent", "timeout": 0.2})
    assert clock.elapsed == pytest.approx(0.6)


def test_negative_packet_window_rejects_delayed_event():
    clock = run.ManualClock()
    runner = runner_with(clock)
    runner.packet_ok = lambda check: clock.now() < 0.15
    with pytest.raises(AssertionError, match="upper bound failed"):
        runner.expect("packet", {"max": 0, "timeout": 0.2})
    assert clock.elapsed == pytest.approx(0.15)
