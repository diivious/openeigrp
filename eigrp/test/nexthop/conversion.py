#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run the real portable next-hop conversion and next-hop-self policy."""
import os
from pathlib import Path
import subprocess

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_nexthop_conversion(tmp_path):
    binary = tmp_path / "nexthop_conversion"
    cmd = [os.environ.get("CC", "cc"), "-std=gnu11", "-Wall", "-Wextra",
           "-Werror", "-Wno-unused-parameter", "-I", str(CODE),
           str(Path(__file__).with_suffix(".c")),
           str(CODE / "eigrp_nexthop.c"), "-o", str(binary)]
    build = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert build.returncode == 0, build.stderr
    tested = subprocess.run([str(binary)], cwd=ROOT, text=True,
                            capture_output=True)
    assert tested.returncode == 0, tested.stderr or tested.stdout


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as tmp:
        test_nexthop_conversion(Path(tmp))
