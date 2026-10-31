#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run IPv4/IPv6 real TLV2 AddPath codec bounds/withdrawal tests."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())


def test_addpath_tlv2_compiled_codec(tmp_path):
    binary = tmp_path / "addpath_codec"
    cc = os.environ.get("CC", "cc")
    flags = ["-ffunction-sections", "-fdata-sections"]
    linker = ["-Wl,-dead_strip"] if sys.platform == "darwin" else ["-Wl,--gc-sections"]
    files = ["eigrp_tlv1.c", "eigrp_tlv2.c", "eigrp_packet.c", "eigrp_ipv4.c",
             "eigrp_ipv6.c", "eigrp_stream.c", "eigrp_prefix.c"]
    subprocess.run([cc, "-std=c11", "-D_POSIX_C_SOURCE=200809L", *flags,
                    f"-I{ROOT}", str(Path(__file__).with_suffix(".c")),
                    *[str(ROOT / "eigrp" / "code" / f) for f in files],
                    *linker, "-o", str(binary)], check=True, cwd=ROOT)
    result = subprocess.run([str(binary)], check=True, cwd=ROOT,
                            text=True, capture_output=True)
    import ipaddress
    import sys as _sys
    _sys.path.insert(0, str(ROOT / "unix" / "test"))
    try:
        from eigrp_wire import _route_addpath_counts, _route_prefixes
        lines = [line.split("|", 4) for line in result.stdout.splitlines()
                 if line.startswith("VECTOR|")]
        assert len(lines) == 20
        for _, af, external, count, hex_bytes in lines:
            wire = bytes(20) + bytes.fromhex(hex_bytes)
            afi = int(af)
            expected = "203.0.113.3/32" if afi == 4 else "2001:db8:abcd::3/128"
            assert _route_prefixes(wire, afi) == [expected]
            actual = _route_addpath_counts(wire, afi)
            assert actual == ({expected: int(count)} if int(count) else {})
    finally:
        _sys.path.remove(str(ROOT / "unix" / "test"))
