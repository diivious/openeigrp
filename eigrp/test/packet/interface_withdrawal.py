#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute actual interface retirement, DUAL, and packetizer code."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())


def test_connected_withdrawal_before_interface_retirement():
    source = Path(__file__).with_suffix(".c")
    libraries = [str(ROOT / "eigrp/code/libeigrp-core.a"),
                 str(ROOT / "libunix/code/libeigrp_uut.a"),
                 str(ROOT / "libunix/code/libeigrp_unix.a")]
    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / "interface_withdrawal"
        link = libraries * 2 if sys.platform == "darwin" else [
            "-Wl,--start-group", *libraries, "-Wl,--end-group"]
        subprocess.run([
            os.environ.get("CC", "cc"), "-std=c11",
            "-D_POSIX_C_SOURCE=200809L", "-DEIGRP_DISABLE_SNMP",
            "-pthread", "-g", "-O0", "-Wall", "-Wextra", "-Werror",
            f"-I{ROOT / 'eigrp/code'}", f"-I{ROOT / 'libunix/code'}",
            str(source), *link, "-o", str(binary)
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    test_connected_withdrawal_before_interface_retirement()
