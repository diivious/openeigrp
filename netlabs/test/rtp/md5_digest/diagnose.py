#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Offline MD5 digest hypotheses for original Cisco StormTrooper PCAPs.

Do not interpret absence of matches as proof of a Cisco digest algorithm.
This diagnostic has no router, network, or OpenEIGRP runtime dependencies.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import struct
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
import wire

EIGRP_HEADER = 20
AUTH_END = 60
DIGEST_START = 44
DIGEST_END = 60
AUTH_FRAME = (2, 40, 2, 16)
# Lab-only known key; never use this fixture value for a real network.
LAB_KEY = 'oe03-lab-md5'
LAB_SOURCE = '10.44.53.2'
LAB_ASN = 4473
LAB_KEY_ID = 7


def require(ok, explanation):
    if not ok:
        raise ValueError(explanation)


def eigrp_checksum(packet):
    """Internet checksum of EIGRP bytes. Zero denotes a valid full packet."""
    if len(packet) & 1:
        packet += b'\x00'
    total = sum(struct.unpack('!%dH' % (len(packet) // 2), packet))
    while total >> 16:
        total = (total & 0xffff) + (total >> 16)
    return (~total) & 0xffff


def captured_packets(path):
    """Yield (timestamp, src, dst, EIGRP) from classic pcap, IPv4 proto 88."""
    with Path(path).open('rb') as stream:
        header = stream.read(24)
        require(len(header) == 24 and header[:4] in wire.PCAP_MAGIC,
                'unsupported or truncated PCAP header')
        order, scale = wire.PCAP_MAGIC[header[:4]]
        major, minor, _, _, snaplen, linktype = struct.unpack(
            order + 'HHiIII', header[4:])
        require((major, minor) == (2, 4) and 0 < snaplen <= 0xfffffff,
                'invalid PCAP metadata')
        while True:
            record = stream.read(16)
            if not record:
                return
            require(len(record) == 16, 'short PCAP record')
            sec, frac, incl, orig = struct.unpack(order + 'IIII', record)
            require(incl <= orig and incl <= snaplen, 'invalid frame lengths')
            frame = stream.read(incl)
            require(len(frame) == incl, 'truncated PCAP frame')
            net = wire.ip_payload(frame, linktype)
            if net and net[0] == 4 and net[3] == 88:
                yield sec + frac * scale, net[1], net[2], net[4]


def decode(packet):
    """Parse EIGRP header, MD5 auth framing, and following TLV boundaries."""
    require(len(packet) >= AUTH_END, 'short authenticated packet')
    fields = struct.unpack_from('!BBHIIIHH', packet)
    version, opcode, checksum, flags, rtp_seq, ack, vrid, asn = fields
    require(version == 2, 'unsupported EIGRP version')
    auth = struct.unpack_from('!HHHHII', packet, EIGRP_HEADER)
    require(auth[:4] == AUTH_FRAME, 'not a canonical MD5 auth TLV')
    require(eigrp_checksum(packet) == 0, 'bad EIGRP packet checksum')
    post = []
    offset = AUTH_END
    while offset < len(packet):
        require(offset + 4 <= len(packet), 'truncated post-auth TLV header')
        tlv_type, length = struct.unpack_from('!HH', packet, offset)
        require(length >= 4 and offset + length <= len(packet),
                'invalid post-auth TLV length')
        item = {'type': tlv_type, 'offset': offset, 'length': length}
        if tlv_type == 1:
            require(length == 12, 'unsupported Parameter TLV length')
            item['k_values'] = list(packet[offset+4:offset+10])
        post.append(item)
        offset += length
    return {'opcode': opcode, 'checksum': checksum, 'flags': flags,
            'rtp_sequence': rtp_seq, 'rtp_ack': ack, 'vrid': vrid, 'asn': asn,
            'key_id': auth[4], 'auth_sequence': auth[5],
            'digest': packet[DIGEST_START:DIGEST_END].hex(),
            'tlvs': post, 'length': len(packet)}


def canonicalize(packet, checksum_mode):
    """Replace the digest with zero and choose an explicit checksum state."""
    p = bytearray(packet)
    p[DIGEST_START:DIGEST_END] = bytes(16)
    if checksum_mode == 'zero':
        p[2:4] = b'\x00\x00'
    elif checksum_mode == 'pre_digest':
        p[2:4] = b'\x00\x00'
        p[2:4] = struct.pack('!H', eigrp_checksum(bytes(p)))
    elif checksum_mode != 'wire':
        raise ValueError('unknown checksum mode')
    return bytes(p)


def candidate_inputs(packet, secret):
    """Yield labelled, falsifiable hypotheses; never guess a matching profile.

    The first two inputs exactly represent OpenEIGRP's current basic Hello and
    UPDATE signing profiles. The remaining, explicitly named alternatives test
    checksum ordering, key placement, TLV coverage and digest-field treatment.
    """
    key = secret.encode('ascii')
    require(0 < len(key) <= 16, 'key must contain 1..16 ASCII bytes')
    key_padded = key.ljust(16, b'\x00')
    for mode in ('zero', 'wire', 'pre_digest'):
        p = canonicalize(packet, mode)
        prefix = p[:DIGEST_START]
        suffix = p[AUTH_END:]
        tlvs = []
        at = AUTH_END
        while at < len(p):
            typ, ln = struct.unpack_from('!HH', p, at)
            tlvs.append((typ, p[at:at+ln]))
            at += ln
        selections = {
            'none': b'',
            'all_postauth': suffix,
            'parameters': b''.join(chunk for typ, chunk in tlvs if typ == 1),
            'software_version': b''.join(chunk for typ, chunk in tlvs if typ == 4),
            'topology_list': b''.join(chunk for typ, chunk in tlvs if typ == 0xf5),
            'except_topology_list': b''.join(chunk for typ, chunk in tlvs if typ != 0xf5),
        }
        for key_name, k in [('padded16', key_padded), ('raw', key)]:
            for selection, tail in selections.items():
                # All hypotheses have literal, independently explainable inputs.
                yield f'{mode}:prefix-key-{selection}:{key_name}', prefix + k + tail
                yield f'{mode}:prefix-{selection}-key:{key_name}', prefix + tail + k
                yield f'{mode}:key-prefix-{selection}:{key_name}', k + prefix + tail
            yield f'{mode}:zero-digest-full-key:{key_name}', p + k
            yield f'{mode}:zero-digest-full-key-before-tlvs:{key_name}', p[:AUTH_END] + k + suffix
            yield f'{mode}:full-digest-overwritten-with-key:{key_name}', prefix + k + suffix
    # Named OpenEIGRP profiles for easy correlation with existing implementation.
    p = canonicalize(packet, 'zero')
    yield 'openeigrp_basic', p[:DIGEST_START] + key_padded
    yield 'openeigrp_update', p[:DIGEST_START] + key_padded + p[AUTH_END:]


def analyze_packet(packet, secret):
    fields = decode(packet)
    target = packet[DIGEST_START:DIGEST_END]
    matches = []
    hypotheses = 0
    for name, data in candidate_inputs(packet, secret):
        hypotheses += 1
        if hashlib.md5(data, usedforsecurity=False).digest() == target:
            matches.append(name)
    fields['candidate_count'] = hypotheses
    fields['matching_profiles'] = sorted(set(matches))
    fields['openeigrp_basic_matches'] = 'openeigrp_basic' in matches
    fields['openeigrp_update_matches'] = 'openeigrp_update' in matches
    fields['input_prefix_hex'] = canonicalize(packet, 'zero')[:DIGEST_START].hex()
    fields['post_auth_hex'] = packet[AUTH_END:].hex()
    fields['confidence'] = 'observed_digest_match' if matches else 'unresolved'
    return fields


def categorize(fields):
    if fields['opcode'] == 1:
        return 'init_update' if fields['length'] == AUTH_END else 'update_with_payload'
    if fields['opcode'] == 5:
        has_f5 = any(t['type'] == 0xf5 for t in fields['tlvs'])
        values = next((t['k_values'] for t in fields['tlvs'] if t['type'] == 1), [])
        return ('hello_topology_goodbye' if values == [255] * 6 else
                'hello_topology') if has_f5 else 'hello_basic'
    return 'other_opcode'


def hello_pair_comparison(packets):
    """Compare topology-list Hello and Goodbye with smallest differing bytes.

    Never attribute a digest difference to K-values without controlling the
    simultaneous checksum difference or knowing Cisco's digest input.
    """
    normal = [pkt for category, pkt in packets if category == 'hello_topology']
    goodbye = [pkt for category, pkt in packets if category == 'hello_topology_goodbye']
    choices = []
    for before in normal:
        for after in goodbye:
            if len(before) != len(after):
                continue
            offsets = [i for i in range(len(before)) if before[i] != after[i]]
            other = [i for i in offsets if i not in (2, 3)
                     and not DIGEST_START <= i < DIGEST_END]
            choices.append((len(other), len(offsets), before, after, offsets, other))
    if not choices:
        return None
    _, _, before, after, offsets, other = min(choices, key=lambda x: (x[0], x[1]))
    return {'packet_length': len(before), 'differing_offsets': offsets,
            'differing_offsets_excluding_checksum_and_digest': other,
            'normal_checksum': before[2:4].hex(),
            'goodbye_checksum': after[2:4].hex(),
            'normal_digest': before[DIGEST_START:DIGEST_END].hex(),
            'goodbye_digest': after[DIGEST_START:DIGEST_END].hex(),
            'interpretation': 'Difference does not by itself identify MD5 input coverage.'}


def analyze_capture(path, secret=LAB_KEY, *, source=LAB_SOURCE,
                    asn=LAB_ASN, key_id=LAB_KEY_ID):
    """Analyze Cisco-originated lab traffic, never imply receive-side success."""
    groups = defaultdict(list)
    total = 0
    pair_packets = []
    for ts, src, dst, pkt in captured_packets(path):
        if src != source or len(pkt) < AUTH_END or pkt[20:22] != b'\x00\x02':
            continue
        fields = analyze_packet(pkt, secret)
        if fields['asn'] != asn or fields['key_id'] != key_id:
            continue
        fields['timestamp'] = round(ts, 6)
        fields['source'] = src
        fields['destination'] = dst
        category = categorize(fields)
        groups[category].append(fields)
        if category in ('hello_topology', 'hello_topology_goodbye'):
            pair_packets.append((category, pkt))
        total += 1
    require(total > 0, 'no Cisco-originated MD5 packets found')
    result = {'capture': Path(path).name,
              'sender': source, 'asn': asn, 'key_id': key_id,
              'packets_total': total, 'categories': {},
              'topology_goodbye_pair': hello_pair_comparison(pair_packets),
              'scope': 'Cisco transmit digest evidence; not Cisco receive behavior'}
    for group, records in sorted(groups.items()):
        sequence_counts = Counter(r['auth_sequence'] for r in records)
        matching = Counter(profile for record in records
                           for profile in record['matching_profiles'])
        k_values = sorted({tuple(t['k_values']) for r in records
                           for t in r['tlvs'] if t['type'] == 1})
        examples = []
        seen = set()
        for item in records:
            # Include distinct observed digest/metadata combinations, not
            # hundreds of identical retransmissions.
            marker = (item['digest'], item['rtp_sequence'], item['flags'])
            if marker in seen:
                continue
            seen.add(marker)
            examples.append(item)
            if len(examples) >= 3:
                break
        result['categories'][group] = {
            'count': len(records),
            'auth_sequences': {str(k): v for k, v in sorted(sequence_counts.items())},
            'openeigrp_basic_matches': sum(r['openeigrp_basic_matches'] for r in records),
            'openeigrp_update_matches': sum(r['openeigrp_update_matches'] for r in records),
            'any_candidate_matches': sum(bool(r['matching_profiles']) for r in records),
            'candidate_match_counts': dict(sorted(matching.items())),
            'k_values': [list(k) for k in k_values], 'examples': examples,
        }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pcap', nargs='+', help='original StormTrooper PCAP(s)')
    parser.add_argument('--secret', default=LAB_KEY, help='lab-only MD5 key')
    parser.add_argument('--source', default=LAB_SOURCE, help='Cisco IPv4 sender')
    parser.add_argument('--asn', type=int, default=LAB_ASN)
    parser.add_argument('--key-id', type=int, default=LAB_KEY_ID)
    parser.add_argument('--json', type=Path, help='write complete JSON evidence')
    args = parser.parse_args(argv)
    reports = [analyze_capture(p, args.secret, source=args.source,
                               asn=args.asn, key_id=args.key_id) for p in args.pcap]
    for report in reports:
        print(f"{report['capture']}: {report['packets_total']} Cisco MD5 packets")
        for name, group in report['categories'].items():
            print(f"  {name}: {group['count']} packets; "
                  f"basic matches {group['openeigrp_basic_matches']}; "
                  f"UPDATE matches {group['openeigrp_update_matches']}; "
                  f"any hypothesis matches {group['any_candidate_matches']}")
    if args.json:
        args.json.write_text(json.dumps(reports, indent=2) + '\n', encoding='utf-8')
        print(f'Wrote {args.json}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
