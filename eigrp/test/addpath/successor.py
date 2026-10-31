#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute real topology successor-change detection across partial withdrawals."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())


def test_addpath_successor_identity_change(tmp_path):
    binary = tmp_path / "addpath_successor"
    linker = ["-Wl,-dead_strip"] if sys.platform == "darwin" else ["-Wl,--gc-sections"]
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                    "-ffunction-sections", "-fdata-sections", "-pthread",
                    f"-I{ROOT / 'eigrp' / 'code'}", str(Path(__file__).with_suffix(".c")),
                    str(ROOT / "eigrp" / "code" / "eigrp_list.c"),
                    *linker, "-o", str(binary)], cwd=ROOT, check=True)
    subprocess.run([str(binary)], check=True, cwd=ROOT)
