#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the portable C metric implementation against classic vectors."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / 'eigrp' / 'code').is_dir())


def test_cisco_classic_metric_arithmetic(tmp_path):
    binary = tmp_path / 'classic_metric'
    cmd = [os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L',
           '-ffunction-sections', '-fdata-sections',
           '-I' + str(ROOT / 'eigrp/code'),
           str(Path(__file__).with_name('classic_metric.c')),
           str(ROOT / 'eigrp/code/eigrp_metric.c'),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           '-o', str(binary)]
    built = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr
    run = subprocess.run([str(binary)], cwd=ROOT, text=True,
                         capture_output=True, timeout=15)
    assert run.returncode == 0, run.stderr
