#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and run an ECMP neighbor-down regression against production DUAL."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_ecmp_withdrawal(tmp_path):
    sources = ['eigrp_query.c', 'eigrp_update.c', 'eigrp_reply.c',
               'eigrp_fsm.c', 'eigrp_topology.c', "eigrp_nexthop.c", 'eigrp_neighbor.c',
               'eigrp_metric.c', 'eigrp_list.c', 'eigrp_table.c',
               'eigrp_prefix.c']
    binary = tmp_path / 'ecmp_withdrawal'
    cmd = [os.getenv('CC', 'cc'), '-std=gnu11', '-DEIGRP_QUERY_LIFECYCLE_TEST',
           '-O0', '-Wall', '-Wextra', '-Werror', '-Wno-unused-function',
           '-Wno-unused-parameter', '-ffunction-sections', '-fdata-sections',
           '-pthread', '-I', str(ROOT), '-I', str(CODE),
           str(Path(__file__).with_suffix('.c')),
           str(ROOT / 'eigrp/test/metric/metric_link_stubs.c'),
           *(str(CODE / name) for name in sources),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           str(ROOT / 'libunix/code/eigrp_unix_format.c'), '-o', str(binary)]
    build = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 0, run.stdout + run.stderr


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as temporary:
        test_ecmp_withdrawal(Path(temporary))
