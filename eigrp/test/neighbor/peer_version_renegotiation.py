# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile portable production code and test hard adjacency restarts."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"
UNIX = ROOT / "libunix" / "code"
SOURCE = Path(__file__).with_suffix(".c")
HOST = SOURCE.with_name("peer_version_test_host.c")


def test_metric_version_and_k_value_restart_lifecycle():
    core = sorted(p for p in CODE.glob("*.c")
                  if p.name not in ("eigrp_dump.c", "eigrp.c"))
    unix = sorted(p for p in UNIX.glob("*.c")
                  if p.name not in ("eigrp_unix_config.c",
                                    "eigrp_uut_wire_broker.c",
                                    "eigrp_unix_keychain.c",
                                    "eigrp_uut_policy.c"))
    includes = [f"-I{CODE}", f"-I{UNIX}"]
    flags = ["-std=c11", "-D_POSIX_C_SOURCE=200809L",
             "-DEIGRP_DISABLE_SNMP", "-pthread", "-ffunction-sections",
             "-fdata-sections"]
    gc = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
    with tempfile.TemporaryDirectory() as tmp:
        executable = Path(tmp) / "peer-version-renegotiation"
        subprocess.run(["cc", *flags, *includes, *map(str, core),
                        *map(str, unix), str(SOURCE), str(HOST), gc,
                        "-o", str(executable)], check=True, cwd=ROOT)
        subprocess.run([str(executable)], check=True, cwd=ROOT)
