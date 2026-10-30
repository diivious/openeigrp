#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the IPv6 peer-limit destination through the real UUT node."""

import subprocess
import sys
from pathlib import Path


def test_ipv6_neighbor_limit_address_decode():
    uut = Path(__file__).resolve().parent
    scenario = uut / "prefix-limit-lifecycle/scenario-peer-ipv6-addr-parse.yaml"
    result = subprocess.run(
        [sys.executable, str(uut / "run.py"),
         str(uut / "examples/ipv6-basic-core.yaml"), str(scenario)],
        text=True, capture_output=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: IPv6 neighbor prefix-limit address decoding under fortified libc" in result.stdout
