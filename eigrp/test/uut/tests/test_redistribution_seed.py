#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Exercise real UUT source-metric selection through the portable core."""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

UUT = Path(__file__).resolve().parents[1]
ROOT = UUT.parents[2]
RUNNER = UUT / "run.py"
TOPOLOGY = UUT / "examples" / "two-router.yaml"
PREFIX = "198.51.100.199/32"


def route(present=True, **fields):
    return {"validate-route": {"uut": "r2", "prefix": PREFIX,
                               "table": "rib", "present": present,
                               "timeout": 15, **fields}}


@pytest.mark.parametrize("seed", [False, True])
def test_redistribute_metric_source_priority(tmp_path, seed):
    steps = [
        {"start": "all"},
        {"validate-neighbor": {"uut": "r1", "neighbor": "10.0.0.2",
                               "state": "up", "timeout": 15}},
        {"source-route-add": {"uut": "r1", "prefix": PREFIX, "metric": 25}},
        {"packet-mark": "redistribute"},
        {"redistribute-add": {"uut": "r1", "source": "static", "seed": seed}},
    ]
    if seed:
        steps.append(route(metric=78643200))
        # A configured per-source seed wins over a later default metric.
        steps.extend([
            {"packet-mark": "default"},
            {"default-metric": {"uut": "r1", "bandwidth": 100000,
                                "delay": 90, "reliability": 255,
                                "load": 1, "mtu": 1500}},
            {"validate-packet": {"uut": "r1", "event": "sent",
                                 "opcode": "update", "prefix": PREFIX,
                                 "since": "default", "min": 0, "max": 0,
                                 "timeout": 0.5}},
            route(metric=78643200),
        ])
    else:
        steps.extend([
            {"validate-packet": {"uut": "r1", "event": "sent",
                                 "opcode": "update", "prefix": PREFIX,
                                 "since": "redistribute", "min": 0,
                                 "max": 0, "timeout": 0.5}},
            route(present=False),
            {"default-metric": {"uut": "r1", "bandwidth": 100000,
                                "delay": 90, "reliability": 255,
                                "load": 1, "mtu": 1500}},
            route(metric=72089600),
            {"default-metric": {"uut": "r1", "reset": True}},
            route(present=False),
        ])
    steps.extend([
        {"source-route-remove": {"uut": "r1", "prefix": PREFIX}},
        route(present=False),
        {"redistribute-remove": {"uut": "r1", "source": "static"}},
        {"stop": "all"},
    ])
    scenario = tmp_path / "redistribution-seed.yaml"
    scenario.write_text(yaml.safe_dump({"schema": "eigrp-uut-test/v1",
                                        "name": f"redistribution-seed-{seed}",
                                        "topology": str(TOPOLOGY),
                                        "steps": steps}, sort_keys=False))
    result = subprocess.run([sys.executable, str(RUNNER), str(TOPOLOGY),
                             str(scenario)], cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=60, check=False)
    assert result.returncode == 0, result.stdout
    assert f"PASS: redistribution-seed-{seed}" in result.stdout


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_seed_option_requires_boolean(value):
    import importlib.util

    spec = importlib.util.spec_from_file_location("eigrp_seed_run", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    scenario = {"schema": mod.TEST_SCHEMA, "topology": str(TOPOLOGY),
                "steps": [{"redistribute-add": {"uut": "r1",
                                                  "source": "static",
                                                  "seed": value}}]}
    with pytest.raises(mod.SpecError, match="seed must be boolean"):
        mod.validate_scenario(scenario)
