#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute the Unix and FRR host formatting implementations."""
from pathlib import Path
import os
import subprocess

ROOT = Path(__file__).resolve().parents[3]


def test_host_formatting_contract(tmp_path):
    # Compile the actual FRR adapter rather than a separate formatter: the
    # Docker daemon links eigrp_frr.c through eigrpd_eigrpd_SOURCES.
    mocks = ROOT / "frr/test/build/include"
    sources = (
        ("unix", "unix/code/eigrp_unix_format.c", []),
        ("frr", "frr/code/eigrp_frr.c", [
            "-D", "EIGRP_STANDALONE_BUILD",
            "-Wno-unused-variable", "-I", str(mocks),
            "-I", str(mocks / "lib"), "-I", str(ROOT / "frr/code")
        ]),
    )
    for host, source, extra in sources:
        binary = tmp_path / ("test_format_" + host)
        command = [
            os.environ.get("CC", "cc"), "-std=gnu11", "-D_POSIX_C_SOURCE=200809L",
            "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
            "-I", str(ROOT / "eigrp/code"), *extra,
            str(ROOT / "eigrp/test/format/test_format.c"), str(ROOT / source),
            "-o", str(binary),
        ]
        build = subprocess.run(command, text=True, capture_output=True, cwd=ROOT)
        assert build.returncode == 0, f"{host}: {build.stderr}"
        run = subprocess.run([str(binary)], text=True, capture_output=True)
        assert run.returncode == 0, f"{host}: {run.stderr}"
        assert "format and copy contract: PASS" in run.stdout


def test_frr_adapter_contract(tmp_path):
    """Exercise renamed FRR adapter APIs in the compiled implementation."""
    mocks = ROOT / "frr/test/build/include"
    binary = tmp_path / "test_frr_adapter"
    command = [
        os.environ.get("CC", "cc"), "-std=gnu11", "-D_POSIX_C_SOURCE=200809L",
        "-Wall", "-Wextra", "-Werror", "-Wno-unused-variable",
        "-Wno-unused-parameter", "-DEIGRP_STANDALONE_BUILD",
        "-I", str(ROOT / "eigrp/code"), "-I", str(ROOT / "frr/code"),
        "-I", str(mocks), "-I", str(mocks / "lib"),
        str(ROOT / "eigrp/test/format/test_frr_adapter.c"),
        str(ROOT / "frr/code/eigrp_frr.c"), "-o", str(binary),
    ]
    build = subprocess.run(command, text=True, capture_output=True, cwd=ROOT)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], text=True, capture_output=True)
    assert run.returncode == 0, run.stderr
    assert "FRR interface adapter: PASS" in run.stdout


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(prefix="eigrp-host-format-") as directory:
        path = Path(directory)
        test_host_formatting_contract(path)
        test_frr_adapter_contract(path)
    print("Host formatting and FRR adapter tests: PASS")
