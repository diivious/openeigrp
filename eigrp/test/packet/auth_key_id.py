#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise portable packet authentication with distinct send/accept keys."""
from pathlib import Path
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / 'eigrp' / 'code').is_dir())
CODE = ROOT / 'eigrp' / 'code'
HARNESS = Path(__file__).with_suffix('.c')


def test_authentication_key_id_and_rollover(tmp_path):
    binary = tmp_path / 'auth-key-id'
    linker_gc = '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'
    cmd = [
        'cc', '-std=c11', '-D_POSIX_C_SOURCE=200809L',
        '-DEIGRP_DISABLE_SNMP', '-Wall', '-Wextra', '-Werror',
        '-Wno-unused-parameter', '-ffunction-sections', '-fdata-sections',
        f'-I{CODE}', str(HARNESS),
        *[str(CODE / f) for f in ('eigrp_md5.c', 'eigrp_sha256.c',
                                    'eigrp_memory.c', 'eigrp_stream.c',
                                    'eigrp_checksum.c')],
        linker_gc, '-pthread', '-o', str(binary),
    ]
    build = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], cwd=ROOT, text=True,
                         capture_output=True, timeout=10)
    assert run.returncode == 0, run.stdout + run.stderr


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as path:
        test_authentication_key_id_and_rollover(Path(path))
    print('authentication key ID rollover: PASS')
