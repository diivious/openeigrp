# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""The classic-metric UUT fixture targets the production metric configuration."""

from pathlib import Path
import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("uut_classic", ROOT / "run.py")
RUN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN)


def test_classic_metric_configuration_is_executed(tmp_path):
    topology = RUN.load_yaml(ROOT / "metric-compatibility/ipv4-classic-parity.yaml")
    RUN.validate_topology(topology)
    runner = RUN.Runner(topology, {}, tmp_path)
    runner.configs()
    for name in topology["routers"]:
        config = (tmp_path / f"{name}.conf").read_text()
        assert "metric-version 32bit\n" in config
        assert "metric-bandwidth " in config
    assert "metric-bandwidth lo1 56\n" in (tmp_path / "r3.conf").read_text()


def test_classic_metric_configuration_rejects_invalid_values():
    topology = RUN.load_yaml(ROOT / "metric-compatibility/ipv4-classic-parity.yaml")
    topology["routers"]["r1"]["metric_version"] = "64bit"
    with pytest.raises(RUN.SpecError, match="metric_version"):
        RUN.validate_topology(topology)
    topology["routers"]["r1"]["metric_version"] = "32bit"
    topology["routers"]["r1"]["interfaces"]["eth12"]["metric_bandwidth"] = 0
    with pytest.raises(RUN.SpecError, match="metric_bandwidth"):
        RUN.validate_topology(topology)


def test_classic_k2_metric_configuration(tmp_path):
    topology = RUN.load_yaml(ROOT / "metric-compatibility/ipv4-classic-k2.yaml")
    RUN.validate_topology(topology)
    runner = RUN.Runner(topology, {}, tmp_path)
    runner.configs()
    for name in topology["routers"]:
        config = (tmp_path / f"{name}.conf").read_text()
        assert "metric-version 32bit\n" in config
        assert "metric-weights 1 1 1 0 0\n" in config


@pytest.mark.parametrize("values", ([1, 1, 1, 0, 256], [1, 1, 1, 0],
                                      [True, 1, 1, 0, 0], "1 1 1 0 0"))
def test_classic_k2_metric_configuration_rejects_invalid_values(values):
    topology = RUN.load_yaml(ROOT / "metric-compatibility/ipv4-classic-k2.yaml")
    topology["routers"]["r1"]["k_values"] = values
    with pytest.raises(RUN.SpecError, match="k_values"):
        RUN.validate_topology(topology)
