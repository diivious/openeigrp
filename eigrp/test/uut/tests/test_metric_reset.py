#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Build the real UUT command handler strictly and exercise metric resets."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

UUT = Path(__file__).resolve().parents[1]
ROOT = UUT.parents[2]
SCENARIO = UUT / "config-propagation" / "metric-reset.yaml"
TOPOLOGY = UUT / "examples" / "two-router.yaml"


def test_metric_reset_compiles_without_uninitialized_reads():
    clang = shutil.which("clang")
    if clang is None:
        pytest.skip("Clang is required for -Wsometimes-uninitialized")
    command = [clang, "-std=c11", "-D_POSIX_C_SOURCE=200809L",
               "-DEIGRP_UUT_TEST", "-DEIGRP_DISABLE_SNMP",
               "-Ieigrp/code", "-Ilibunix/code", "-O2", "-Wall", "-Wextra",
               "-Wno-unused-parameter", "-Wno-unused-const-variable",
               "-Werror=sometimes-uninitialized", "-fsyntax-only",
               "eigrp/test/uut/node.c"]
    result = subprocess.run(command, cwd=ROOT, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stdout


def test_metric_resets_execute_real_uut():
    result = subprocess.run([sys.executable, str(UUT / "run.py"),
                             str(TOPOLOGY), str(SCENARIO)], cwd=ROOT,
                            text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=90,
                            check=False)
    assert result.returncode == 0, result.stdout
    assert "PASS: ipv4-redistribution-metric-reset-lifecycle" in result.stdout
