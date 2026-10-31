#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Validate AddPath UUT setup, diagnostic predicates, and negative controls."""
from pathlib import Path
import importlib.util
import sys
import pytest

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())
RUN = ROOT / "eigrp/test/uut/run.py"
spec = importlib.util.spec_from_file_location("addpath_uut_run", RUN)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
sys.path.insert(0, str(ROOT / "unix/test"))
from eigrp_wire import _route_addpath_counts


def test_addpath_topology_rejects_invalid_interface_configuration():
    t = runner.load_yaml(ROOT / "eigrp/test/uut/addpath/ipv4-diamond-hub.yaml")
    assert runner.validate_topology(t) == t
    for value in (-1, 0, 5, True, "2"):
        t["routers"]["hub"]["interfaces"]["eth15"]["add_paths"] = value
        with pytest.raises(runner.SpecError, match="add_paths"):
            runner.validate_topology(t)
    t["routers"]["hub"]["interfaces"]["eth15"]["add_paths"] = 2
    for value in (0, 1, "false", None):
        t["routers"]["hub"]["interfaces"]["eth15"]["next_hop_self"] = value
        with pytest.raises(runner.SpecError, match="next_hop_self"):
            runner.validate_topology(t)


def test_addpath_packet_assertion_and_live_config_reject_invalid_values():
    s = runner.load_yaml(ROOT / "eigrp/test/uut/addpath/scenario-ipv4-burst-withdraw-recover.yaml")
    assert runner.validate_scenario(s) == s
    for value in (-1, 5, True, "1"):
        wrong = {"schema": runner.TEST_SCHEMA, "topology": "test.yaml",
                 "steps": [{"add-paths-config": {"uut": "hub", "interface": "eth15", "count": value}}]}
        with pytest.raises(runner.SpecError):
            runner.validate_scenario(wrong)
    for args in ({"addpaths_min": 5}, {"addpaths_min": 2, "addpaths_max": 1},
                 {"addpaths_min": -1}, {"addpaths_min": True}):
        base = {"uut": "hub", "opcode": "update", "event": "delivered", **args}
        with pytest.raises(runner.SpecError):
            runner.validate_packet_assertion(base, "addpath test")


def test_addpath_virtual_wire_rejects_cross_family_and_truncated_tlv():
    import ipaddress
    import struct

    prefix = bytes([32]) + ipaddress.ip_address("203.0.113.3").packed
    attr = bytes([7, 6]) + ipaddress.ip_address("192.0.2.2").packed + bytes(8)
    body = struct.pack("!HHI", 0, 1, 0) + bytes([7]) + bytes(23) + attr + bytes(4) + prefix
    tlv = struct.pack("!HH", 0x0602, 4 + len(body)) + body
    assert _route_addpath_counts(bytes(20) + tlv, 4) == {"203.0.113.3/32": 1}
    assert _route_addpath_counts(bytes(20) + tlv, 6) == {}
    # Word count 7 carries only 14 bytes; the original 12-byte payload is
    # framed by 6 words, so reject attempts to eat the next field.
    malformed = bytearray(tlv)
    malformed[4 + 8] = 8
    assert _route_addpath_counts(bytes(20) + malformed, 4) == {}
    malformed = bytearray(tlv)
    malformed[4 + 8 + 24 + 1] = 20
    assert _route_addpath_counts(bytes(20) + malformed, 4) == {}
    assert _route_addpath_counts(bytes(20) + tlv[:-1], 4) == {}
