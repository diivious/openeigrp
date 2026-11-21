#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile actual auth writers and TLV encoders; exercise AF/ingress threads."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CODE = Path(__file__).resolve().parents[2] / "code"


def test_auth_reconfiguration_race():
    src = Path(__file__).with_name("auth_reconfig_race.c")
    flags = ["-std=c11", "-D_POSIX_C_SOURCE=200809L", "-DEIGRP_DISABLE_SNMP",
             "-pthread", "-Wall", "-Wextra", "-Werror", "-ffunction-sections",
             "-fdata-sections", f"-I{CODE}"]
    if os.getenv("EIGRP_AUTH_SANITIZE"):
        flags += ["-g", "-fsanitize=address", "-fno-omit-frame-pointer"]
    gc = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
    with tempfile.TemporaryDirectory() as folder:
        binary = Path(folder) / "auth_reconfig_race"
        subprocess.run([os.getenv("CC", "cc"), *flags, str(src),
                        str(CODE / "eigrp_auth.c"), str(CODE / "eigrp_stream.c"),
                        str(CODE / "eigrp_interface.c"), str(CODE / "eigrp_prefix.c"), gc,
                        "-o", str(binary)], check=True)
        subprocess.run([str(binary)], check=True)


if __name__ == "__main__":
    test_auth_reconfiguration_race()
