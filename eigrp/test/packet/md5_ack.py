# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise ingress admission with Cisco's captured unauthenticated MD5 ACK."""
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"


def test_cisco_md5_header_only_ack_ingress():
    source = Path(__file__).with_suffix('.c')
    fixture = Path(__file__).parent / 'fixtures' / 'cisco-md5-ack-eot.bin'
    flags = ['-std=gnu11', '-O2', '-D_POSIX_C_SOURCE=200809L',
             '-DEIGRP_DISABLE_SNMP', '-pthread', '-ffunction-sections',
             '-fdata-sections', f'-I{CODE}']
    gc = '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'
    with tempfile.TemporaryDirectory() as tmp:
        binary = Path(tmp) / 'md5_ack'
        subprocess.run(["cc", *flags, str(source), gc, '-o', str(binary)], check=True)
        subprocess.run([str(binary), str(fixture)], check=True)
