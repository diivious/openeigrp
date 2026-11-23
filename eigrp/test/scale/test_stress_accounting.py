#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression coverage for scale topology and learned-RIB accounting."""

import contextlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "eigrp_scale_stress_accounting", Path(__file__).with_name("stress_extended.py"))
if spec is None or spec.loader is None:
    raise ImportError("unable to import scale stress test")
stress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stress)


class RouteState:
    def __init__(self, count):
        self.active = {stress.prefix(i) for i in range(count)}
        self.proc = SimpleNamespace(pid=1)

    def state_counts(self, timeout=240):
        # The connected transit prefix exists in topology, not learned RIB.
        return ({"TOPO": len(self.active) + 1, "RIB": len(self.active)},
                {"10.41.0.1"}, [("eth0", 0, 0)])

    def alive(self):
        pass


@contextlib.contextmanager
def fake_nodes(state):
    yield {"r1": state, "r2": state}, object()


class ScaleAccountingTest(unittest.TestCase):
    def run_case(self, name, count, churn_routes=3):
        state = RouteState(count)

        def command(node, cmd, timeout=120):
            verb, route, *rest = cmd.split()
            if verb == "SOURCE-DEL":
                state.active.remove(route)
            elif verb == "SOURCE-ADD":
                state.active.add(route)
            else:
                self.fail(f"unexpected command: {cmd}")

        output = io.StringIO()
        with patch.object(stress, "ROUTES", count), \
             patch.object(stress, "CHURN_ROUTES", churn_routes), \
             patch.object(stress, "CHURN_CYCLES", 2), \
             patch.object(stress, "WATCHDOG", 0.2), \
             patch.object(stress, "paired_nodes", lambda *a, **k: fake_nodes(state)), \
             patch.object(stress, "command", command), \
             patch.object(stress, "rss_kib", lambda pid: 1), \
             patch.object(stress, "check_live", lambda *a, **k: None), \
             patch.object(stress.time, "sleep", lambda seconds: None), \
             contextlib.redirect_stdout(output):
            getattr(stress, name)()
        self.assertEqual(len(state.active), count)
        self.assertIn(f"PASS scale/{name}", output.getvalue())
        self.assertIn(f"RIB={count}", output.getvalue())

    def test_withdraw_10(self):
        self.run_case("withdraw", 10)

    def test_withdraw_100(self):
        self.run_case("withdraw", 100)

    def test_churn_two_cycles(self):
        self.run_case("churn", 10)

    def test_wait_rejects_missing_learned_route(self):
        state = RouteState(9)
        with patch.object(stress.time, "sleep", lambda seconds: None), \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(AssertionError, "route/peer state stalled"):
                stress.wait_for(state, 11, stress.time.monotonic() + 0.02,
                                "missing route", maximum=11, min_peers=1,
                                min_rib=10, max_rib=10)


if __name__ == "__main__":
    unittest.main()
