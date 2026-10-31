#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute the portable topology next-hop conversion."""

from pathlib import Path
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_addpath_rib_nexthop_address_family_boundary(tmp_path):
    binary = tmp_path / "addpath_nexthop"
    linker = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
    subprocess.run(
        ["cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DEIGRP_DISABLE_SNMP",
         "-pthread", "-ffunction-sections", "-fdata-sections", f"-I{CODE}",
         str(Path(__file__).with_suffix(".c")), linker, "-o", str(binary)],
        check=True,
    )
    subprocess.run([str(binary)], check=True)
