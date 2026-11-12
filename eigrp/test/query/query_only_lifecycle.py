# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression for issue #45 using production receive, DUAL and topology C."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_query_only_lifecycle(tmp_path):
    sources = ['eigrp_query.c', 'eigrp_update.c', 'eigrp_reply.c',
               'eigrp_fsm.c', 'eigrp_topology.c', 'eigrp_neighbor.c',
               'eigrp_metric.c', 'eigrp_list.c', 'eigrp_table.c',
               'eigrp_prefix.c']
    binary = tmp_path / 'query_only_lifecycle'
    cmd = [os.getenv('CC', 'cc'), '-std=gnu11', '-DEIGRP_QUERY_LIFECYCLE_TEST', '-O0', '-Wall', '-Wextra',
           '-Werror', '-Wno-unused-function', '-Wno-unused-parameter',
           '-ffunction-sections', '-fdata-sections', '-pthread',
           '-I', str(ROOT), '-I', str(CODE),
           str(Path(__file__).with_suffix('.c')),
           str(ROOT / "eigrp/test/metric/metric_link_stubs.c"),
           *(str(CODE / name) for name in sources),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           str(ROOT / 'libunix/code/eigrp_unix_format.c'), '-o', str(binary)]
    build = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True)
    assert run.returncode == 0, run.stdout + run.stderr
