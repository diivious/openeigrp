#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""OE-08: deterministic allocation-failure tests of real topology storage."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = next(path for path in Path(__file__).resolve().parents if (path / "eigrp" / "code").is_dir())


def test_topology_allocation_failure(tmp_path):
    source = ROOT / "eigrp/test/topology/allocation_failure.c"
    binary = tmp_path / "topology_allocation_failure"
    command = [
        os.environ.get("CC", "cc"),
        "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
        "-Wno-unused-parameter", "-ffunction-sections", "-fdata-sections",
        "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections",
        "-Dcalloc=eigrp_test_calloc", "-pthread",
        "-Ieigrp/code", "-Ieigrp/test/uut",
        str(source),
        "eigrp/code/eigrp_list.c",
        "eigrp/code/eigrp_table.c",
        "eigrp/code/eigrp_topology.c",
        "eigrp/code/eigrp_prefix.c",
        "-o", str(binary),
    ]
    subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    subprocess.run([str(binary)], cwd=ROOT, check=True, capture_output=True, text=True)
