#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and exercise the portable runtime topology-ID admission rule."""
from pathlib import Path
import os
import socket
import struct
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'eigrp' / 'code').is_dir())
CODE = ROOT / 'eigrp' / 'code'


def test_topology_id_isolation(tmp_path):
    binary = tmp_path / 'topology_id_isolation'
    cmd = [os.environ.get('CC', 'cc'), '-std=gnu11', '-O0', '-Wall', '-Wextra',
           '-Werror', '-Wno-unused-parameter', '-ffunction-sections',
           '-fdata-sections', '-I', str(CODE),
           str(Path(__file__).with_suffix('.c')),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           '-pthread', '-o', str(binary)]
    build = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert run.returncode == 0, run.stderr


def test_wire_topology_id_fault():
    """Confirm the UUT fault changes only the requested TLV and stays valid."""
    sys.path.insert(0, str(ROOT / 'libunix' / 'test'))
    import eigrp_wire as wire

    def tlv(network):
        raw = socket.inet_aton(network)
        dest = bytes([24]) + raw[:3]
        metric = bytes(24)
        body = struct.pack('!HHI', 0, 1, 0x0a000002) + metric + bytes(4) + dest
        return struct.pack('!HH', 0x0602, 4 + len(body)) + body

    packet = bytes([2, 1]) + bytes(18) + tlv('198.51.100.0') + tlv('203.0.113.0')
    src = socket.inet_aton('10.0.0.2') + bytes(12)
    dst = socket.inet_aton('224.0.0.10') + bytes(12)
    frame = wire.HDR.pack(1, wire.PACKET, 4, wire.MULTICAST, 1,
                          7, 4, len(packet), 0, src, dst) + b'transiteth0' + packet
    changed = wire._topology_id_set(frame, 4, 65535, '198.51.100.0/24')
    offset = wire.HDR.size + 7 + 4 + 20
    assert struct.unpack_from('!H', changed, offset+4)[0] == 65535
    offset += len(tlv('198.51.100.0'))
    assert struct.unpack_from('!H', changed, offset+4)[0] == 0
    assert wire._route_prefixes(changed[wire.HDR.size+11:], 4) == [
        '198.51.100.0/24', '203.0.113.0/24']
    payload = changed[wire.HDR.size+11:]
    total = sum((payload[i]<<8)|payload[i+1] for i in range(0,len(payload),2))
    while total>>16:
        total = (total&0xffff)+(total>>16)
    assert total == 0xffff
    assert wire._topology_id_set(frame, 4, 3, '192.0.2.0/24') == frame


if __name__ == '__main__':
    import tempfile
    with tempfile.TemporaryDirectory() as path:
        test_topology_id_isolation(Path(path))
    test_wire_topology_id_fault()
    print('topology ID isolation: PASS')
