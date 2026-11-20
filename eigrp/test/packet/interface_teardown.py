#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Execute the actual portable interface teardown and timer implementation."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())


def test_interface_teardown_timer_and_write_queue():
    source = Path(__file__).with_suffix(".c")
    libraries = [str(ROOT / "eigrp/code/libeigrp-core.a"),
                 str(ROOT / "libunix/code/libeigrp_uut.a"),
                 str(ROOT / "libunix/code/libeigrp_unix.a")]
    if sys.platform == "darwin":
        flags = []
        link = libraries * 2
    else:
        flags = ["-DEIGRP_TEST_WRAP_TOPOLOGY",
                 "-Wl,--wrap=eigrp_topology_connected_interface_down"]
        link = ["-Wl,--start-group", *libraries, "-Wl,--end-group"]

    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / "interface_teardown"
        subprocess.run([
            "cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L",
            "-DEIGRP_DISABLE_SNMP", "-pthread", "-g", "-O0",
            f"-I{ROOT / 'eigrp/code'}", f"-I{ROOT / 'libunix/code'}",
            *flags, str(source), *link, "-o", str(binary)
        ], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    test_interface_teardown_timer_and_write_queue()
