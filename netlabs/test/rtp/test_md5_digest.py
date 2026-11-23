#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable regression tests against original Cisco StormTrooper MD5 frames."""
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys

import pytest

HERE = Path(__file__).resolve().parent
ANALYZER = HERE / 'md5_digest' / 'diagnose.py'
FIXTURES = HERE / 'fixtures'
spec = importlib.util.spec_from_file_location('md5_diagnose', ANALYZER)
md5 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(md5)


def fixture(name):
    return FIXTURES / f'csr1000v-17.03.06-md5-{name}.pcap'


def cisco_packets(name):
    return [(when, pkt) for when, src, _dst, pkt in md5.captured_packets(fixture(name))
            if src == md5.LAB_SOURCE and len(pkt) >= 60 and pkt[20:22] == b'\x00\x02']


def first_packet(name, category):
    for _when, pkt in cisco_packets(name):
        if md5.categorize(md5.decode(pkt)) == category:
            return pkt
    raise AssertionError(f'no packet in {name}: {category}')


@pytest.mark.parametrize('name,basic,topology,goodbye,init', [
    ('hello', 4, 2, 1, 0),
    ('named', 12, 31, 2, 37),
    ('classic', 10, 32, 2, 38),
])
def test_original_cisco_captures(name, basic, topology, goodbye, init):
    report = md5.analyze_capture(fixture(name))
    assert report['packets_total'] == basic + topology + goodbye + init
    observed = report['categories']
    assert observed['hello_basic']['count'] == basic
    assert observed['hello_basic']['openeigrp_basic_matches'] == basic
    assert observed['hello_topology']['count'] == topology
    assert observed['hello_topology']['any_candidate_matches'] == 0
    assert observed['hello_topology_goodbye']['count'] == goodbye
    assert observed['hello_topology_goodbye']['any_candidate_matches'] == 0
    if init:
        assert observed['init_update']['count'] == init
        assert observed['init_update']['any_candidate_matches'] == 0
        assert observed['init_update']['auth_sequences'] == {'0': init}
    assert all('0' in cat['auth_sequences'] for cat in observed.values())


@pytest.mark.parametrize('name', ['hello', 'named', 'classic'])
def test_topology_goodbye_pair_has_checksum_kvalue_digest_differences(name):
    pair = md5.analyze_capture(fixture(name))['topology_goodbye_pair']
    assert pair is not None
    assert pair['packet_length'] == 90
    assert pair['differing_offsets_excluding_checksum_and_digest'] == list(range(64, 70))
    assert pair['normal_digest'] != pair['goodbye_digest']
    assert pair['normal_checksum'] != pair['goodbye_checksum']


def test_ordinary_hello_keyed_digest_excludes_parameter_values():
    original = first_packet('hello', 'hello_basic')
    changed = bytearray(original)
    assert changed[64:70] == bytes([1, 0, 1, 0, 0, 0])
    changed[64:70] = b'\xff' * 6
    changed[2:4] = b'\x00\x00'
    changed[2:4] = struct.pack('!H', md5.eigrp_checksum(bytes(changed)))
    assert md5.eigrp_checksum(bytes(changed)) == 0
    assert original[44:60] == changed[44:60]
    assert md5.analyze_packet(original, md5.LAB_KEY)['openeigrp_basic_matches']
    assert md5.analyze_packet(bytes(changed), md5.LAB_KEY)['openeigrp_basic_matches']
    # No claim about Cisco receive-side acceptance of modified Hellos.


def test_known_key_is_required_for_ordinary_hello():
    packet = first_packet('classic', 'hello_basic')
    assert md5.analyze_packet(packet, md5.LAB_KEY)['openeigrp_basic_matches']
    assert not md5.analyze_packet(packet, 'wrong-lab-key')['matching_profiles']


def test_capture_checksum_validates_real_cisco_packets():
    for name in ('hello', 'named', 'classic'):
        for _timestamp, packet in cisco_packets(name):
            assert md5.eigrp_checksum(packet) == 0


def test_modified_checksum_is_rejected():
    packet = bytearray(first_packet('classic', 'init_update'))
    packet[3] ^= 1
    with pytest.raises(ValueError, match='checksum'):
        md5.decode(bytes(packet))


def test_invalid_auth_and_tlv_bounds_rejected():
    pkt = bytearray(first_packet('classic', 'hello_basic'))
    pkt[22:24] = b'\x00\x28'   # keep auth framing, damage TLV length below
    pkt[62:64] = b'\xff\xff'
    pkt[2:4] = b'\x00\x00'
    pkt[2:4] = struct.pack('!H', md5.eigrp_checksum(bytes(pkt)))
    with pytest.raises(ValueError, match='invalid post-auth TLV length'):
        md5.decode(bytes(pkt))
    pkt = bytearray(first_packet('classic', 'hello_basic'))
    pkt[20:22] = b'\x00\x01'
    pkt[2:4] = b'\x00\x00'
    pkt[2:4] = struct.pack('!H', md5.eigrp_checksum(bytes(pkt)))
    with pytest.raises(ValueError, match='canonical MD5'):
        md5.decode(bytes(pkt))


def test_captured_rtp_and_auth_sequences_are_not_conflated():
    packet = first_packet('classic', 'init_update')
    data = md5.decode(packet)
    assert data['opcode'] == 1
    assert data['rtp_sequence'] != 0
    assert data['auth_sequence'] == 0


def test_corrupted_pcap_fails_closed(tmp_path):
    bad = tmp_path / 'truncated.pcap'
    bad.write_bytes(fixture('classic').read_bytes()[:-1])
    with pytest.raises(ValueError, match='truncated'):
        list(md5.captured_packets(bad))


def test_command_line_exports_json_without_claiming_cisco_receive(tmp_path):
    output = tmp_path / 'results.json'
    result = subprocess.run([sys.executable, str(ANALYZER),
                             str(fixture('classic')), '--json', str(output)],
                            capture_output=True, text=True, check=True)
    assert 'init_update: 38' in result.stdout
    rows = json.loads(output.read_text(encoding='utf-8'))
    assert rows[0]['scope'].startswith('Cisco transmit digest evidence')
    assert rows[0]['categories']['init_update']['any_candidate_matches'] == 0
    assert 'secret' not in rows[0]
