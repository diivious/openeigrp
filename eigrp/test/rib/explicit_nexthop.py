#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the real topology -> portable RIB forwarding snapshot."""

import os
from pathlib import Path
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_explicit_nexthop_forwarding(tmp_path):
    binary = tmp_path / "explicit_nexthop"
    command = [
        os.environ.get("CC", "cc"), "-std=gnu11", "-O0", "-Wall", "-Wextra",
        "-Werror", "-Wno-unused-function", "-Wno-unused-parameter",
        "-ffunction-sections", "-fdata-sections", f"-I{ROOT}",
        str(Path(__file__).with_suffix(".c")),
        *(str(CODE / source) for source in (
            "eigrp_topology.c", "eigrp_nexthop.c", "eigrp_timer.c", "eigrp_list.c",
            "eigrp_table.c", "eigrp_prefix.c")),
        *( ["-Wl,-dead_strip"] if sys.platform == "darwin"
            else ["-Wl,--gc-sections"]),
        "-pthread", "-I", str(CODE),
        str(ROOT / "libunix/code/eigrp_unix_format.c"),
        "-o", str(binary),
    ]
    built = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
    assert built.returncode == 0, built.stderr
    tested = subprocess.run([str(binary)], cwd=ROOT, capture_output=True,
                            text=True)
    assert tested.returncode == 0, tested.stderr or tested.stdout


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as work:
        test_explicit_nexthop_forwarding(Path(work))
