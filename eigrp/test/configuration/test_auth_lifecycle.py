# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile real portable interface and authentication targets as a unit."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_authentication_config_lifecycle():
    src = Path(__file__).with_name("auth_lifecycle_harness.c")
    flags = ["-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DEIGRP_DISABLE_SNMP",
             "-pthread", "-ffunction-sections", "-fdata-sections", f"-I{CODE}"]
    gc = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / "auth_config_lifecycle"
        subprocess.run(["cc", *flags, str(src),
                        str(CODE / "eigrp_interface.c"), str(CODE / "eigrp_auth.c"),
                        str(CODE / "eigrp_list.c"), str(CODE / "eigrp_prefix.c"), gc, "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)
