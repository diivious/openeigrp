# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Exercise UUT convergence synchronization and real AF state snapshots."""
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest


UUT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eigrp_uut_run_sync", UUT / "run.py")
runner_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner_module)


class SnapshotNode:
    def __init__(self, snapshots):
        self.snapshots = snapshots
        self.calls = 0

    def state(self):
        current = self.snapshots[min(self.calls, len(self.snapshots) - 1)]
        self.calls += 1
        return current


def state(count, present=False):
    return {
        "neighbors": [], "accounting": [], "source": [], "rib": [],
        "topology": [], "paths": ([{"prefix": "192.0.2.221/32"}] if present else []),
        "redist_limit": {"state": 2, "restarts": 3, "count": count,
                         "restart_active": False, "reset_active": False, "penalty": 0},
    }


CONVERGENCE = {
    "timeout": 0.7, "stable_for": 0.2,
    "expect": [
        {"redist-limit": {"uut": "r1", "state": 2, "restarts": 3, "count": 0}},
        {"route": {"uut": "r1", "prefix": "192.0.2.221/32",
                   "table": "path", "present": False}},
    ],
}


def test_convergence_rejects_partial_cleanup_then_waits_for_stability():
    # Delayed retirement can report count=0 and still have a route, or vice
    # versa. One sample per UUT must satisfy every condition at once.
    node = SnapshotNode([state(1, True), state(0, True), state(1, False),
                         state(0, False)])
    runner = runner_module.Runner.__new__(runner_module.Runner)
    runner.nodes = {"r1": node}
    runner.expect_convergence(CONVERGENCE)
    assert node.calls >= 6


def test_convergence_does_not_accept_transient_empty_topology():
    node = SnapshotNode([state(0, False), state(1, True)])
    runner = runner_module.Runner.__new__(runner_module.Runner)
    runner.nodes = {"r1": node}
    with pytest.raises(AssertionError, match="count.*1"):
        runner.expect_convergence({**CONVERGENCE, "timeout": 0.35})
    assert node.calls >= 2


@pytest.mark.parametrize("invalid", [
    {"redist-limit": {"uut": "r1", "count": False}},
    {"redist-limit": {"uut": "r1", "unknown": 1}},
    {"redist-limit": {"uut": "r1"}},
    {"redist-limit": {"uut": "r1", "state": 3}},
])
def test_convergence_rejects_invalid_limit_filters(invalid):
    with pytest.raises(runner_module.SpecError):
        runner_module.validate_scenario({
            "schema": runner_module.TEST_SCHEMA, "topology": "two-router.yaml",
            "name": "reject-invalid-convergence",
            "steps": [{"validate-convergence": {"expect": [invalid]}}],
        })


def test_live_redistribution_lockout_recovery():
    """The real UUT must withdraw, retire, replay and recover on both routers."""
    scenario = UUT / "prefix-limit/scenario-redist-lockout-ipv4.yaml"
    topology = UUT / "examples/two-router.yaml"
    result = subprocess.run(
        [sys.executable, str(UUT / "run.py"), str(topology), str(scenario)],
        capture_output=True, text=True, timeout=55,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_convergence_supports_process_and_neighbor_accounting():
    snapshot = state(0)
    snapshot["process_limit"] = {"state": 0, "restarts": 0, "count": 1,
                                  "restart_active": False, "reset_active": False,
                                  "penalty": 0}
    snapshot["accounting"] = [{"address": "10.0.0.2", "interface": "eth0",
                                "status": "A", "prefix_count": 1,
                                "restart_count": 0, "restart_active": False,
                                "reset_active": False}]
    node = SnapshotNode([snapshot])
    runner = runner_module.Runner.__new__(runner_module.Runner)
    runner.nodes = {"r1": node}
    check = {"timeout": 0.4, "stable_for": 0.1, "expect": [
        {"process-limit": {"uut": "r1", "state": 0, "count": 1}},
        {"accounting": {"uut": "r1", "neighbor": "10.0.0.2",
                        "interface": "eth0", "prefix_count": 1}},
    ]}
    runner_module.validate_scenario({"schema": runner_module.TEST_SCHEMA,
                                     "topology": "two-router.yaml", "name": "process-sync",
                                     "steps": [{"validate-convergence": check}]})
    runner.expect_convergence(check)
    assert node.calls >= 2


@pytest.mark.parametrize("window", [
    {"timeout": 0},
    {"timeout": float("nan")},
    {"timeout": 0.5, "stable_for": 0.5},
    {"timeout": 1, "stable_for": float("inf")},
])
def test_convergence_rejects_invalid_windows(window):
    with pytest.raises(runner_module.SpecError):
        runner_module.validate_scenario({"schema": runner_module.TEST_SCHEMA,
                                         "topology": "two-router.yaml", "name": "window",
                                         "steps": [{"validate-convergence": {
                                             "expect": [{"redist-limit": {"uut": "r1", "count": 0}}],
                                             **window}}]})
