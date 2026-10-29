#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Exercise real peer teardown and deferred DUAL withdrawal twice."""

import subprocess
import sys
from pathlib import Path


def test_peer_retirement_after_interface_flaps():
    uut = Path(__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, str(uut / "run.py"),
         str(uut / "examples/two-router.yaml"),
         str(uut / "examples/two-router-neighbor-route.yaml")],
        text=True, capture_output=True, timeout=50,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: two-router-framework-smoke" in result.stdout
