#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile the portable AddPath selector with bounded topology fixtures."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())


def test_addpath_selection_compiled(tmp_path):
    binary = tmp_path / "addpath_selection"
    linker = ["-Wl,-dead_strip"] if sys.platform == "darwin" else ["-Wl,--gc-sections"]
    subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-D_POSIX_C_SOURCE=200809L",
                    "-ffunction-sections", "-fdata-sections", f"-I{ROOT}",
                    str(Path(__file__).with_suffix(".c")),
                    str(ROOT / "eigrp" / "code" / "eigrp_addpath.c"),
                    *linker, "-o", str(binary)], cwd=ROOT, check=True)
    subprocess.run([str(binary)], check=True, cwd=ROOT)
