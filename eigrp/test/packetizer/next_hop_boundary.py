#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the production packetizer's packet-full next-hop-self retry."""
from pathlib import Path
import os
import platform
import subprocess

ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_next_hop_self_packet_retry(tmp_path):
    binary = tmp_path / "next_hop_boundary"
    command = [os.environ.get("CC", "cc"), "-std=gnu11", "-O2", "-Wall",
               "-Wextra", "-Werror", "-Wno-unused-parameter",
               "-ffunction-sections", "-fdata-sections", "-I", str(CODE),
               str(Path(__file__).with_suffix(".c")),
               str(CODE / "eigrp_nexthop.c"),
               ("-Wl,-dead_strip" if platform.system() == "Darwin"
                else "-Wl,--gc-sections"), "-o", str(binary)]
    subprocess.run(command, cwd=ROOT, check=True)
    subprocess.run([str(binary)], cwd=ROOT, check=True)


def test_next_hop_darwin_linker_flag(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    monkeypatch.setattr(subprocess, "run",
                        lambda command, **kwargs: commands.append(command))

    test_next_hop_self_packet_retry(tmp_path)
    assert len(commands) == 2
    assert "-Wl,-dead_strip" in commands[0]
    assert "-Wl,--gc-sections" not in commands[0]


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        test_next_hop_self_packet_retry(Path(directory))
