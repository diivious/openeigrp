#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the CI SIGABRT hook and verify it preserves the native failure."""

import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent


def test_linux_abort_hook_reports_stack_and_preserves_sigabrt(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux-only diagnostic")
    compiler = os.environ.get("CC", "cc")
    library = tmp_path / "abort_trace.so"
    probe = tmp_path / "abort_probe.c"
    executable = tmp_path / "abort_probe"
    probe.write_text("#include <stdlib.h>\n"
                     "__attribute__((noinline)) void trigger(void) { abort(); }\n"
                     "int main(void) { trigger(); }\n")
    for command in (
        [compiler, "-O2", "-g", "-shared", "-fPIC", "-o", str(library),
         str(HERE / "abort_trace.c")],
        [compiler, "-O2", "-g", "-rdynamic", "-o", str(executable), str(probe)],
    ):
        built = subprocess.run(command, capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
    env = os.environ.copy()
    env["LD_PRELOAD"] = str(library)
    # Ensure the abort hook doesn't swallow SIGABRT, falsely passing a UUT.
    ran = subprocess.run([str(executable)], env=env, capture_output=True,
                         text=True, timeout=10)
    assert ran.returncode == -signal.SIGABRT
    assert "=== UUT SIGABRT native backtrace" in ran.stderr
    assert "abort" in ran.stderr
