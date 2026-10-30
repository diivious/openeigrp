#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Live two-router RTP/wire interoperability qualification.

No result is PASS unless the relevant bidirectional wire assertions execute.
The remote runner owns netlab teardown and recovers capture/fault markers.
"""
import argparse
import ipaddress
from collections import Counter
import json
from pathlib import Path
import re
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
from interop import Lab, Trace, command, fault, report_base, result, write_report
from interop import CapturePrerequisiteError, require_packet_access, rib_prefixes
import wire
import network_fault

CASES = ('RTP-01', 'RTP-02', 'RTP-03', 'RTP-04', 'RTP-05',
         'RTP-06', 'RTP-07', 'RTP-08', 'RTP-11')
ORIGINS = {'oe': {'ipv4': '10.44.53.1', 'ipv6': None},
           'ce': {'ipv4': '10.44.53.2', 'ipv6': None}}
MULTICAST = {'ipv4': '224.0.0.10', 'ipv6': 'ff02::a'}


def by_role(events, family, role, sources):
    return [e for e in events if e['family'] == family and e['source'] == sources[role][family]]


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def startup(events, family, sources):
    """Require both-way multicast discovery, NULL INIT, ACK and EOT evidence."""
    for sender in ('oe', 'ce'):
        receiver = 'ce' if sender == 'oe' else 'oe'
        packets = by_role(events, family, sender, sources)
        require(any(e['opcode_name'] == 'HELLO' and
                    e['destination'] == MULTICAST[family] and e['sequence'] == 0
                    for e in packets), sender + ' multicast discovery HELLO missing')
        init = [e for e in packets if e['opcode_name'] == 'UPDATE' and
                'INIT' in e['flag_names'] and e['destination'] == sources[receiver][family]]
        require(init, sender + ' unicast INIT UPDATE missing')
        require(any(not any(t.get('route_kind') for t in e['tlvs']) for e in init),
                sender + ' never sent a null INIT UPDATE')
        for update in init:
            require(update['sequence'] != 0, 'INIT must be reliable')
            require(any(e['ack'] == update['sequence'] and
                        e['destination'] == sources[sender][family]
                        for e in by_role(events, family, receiver, sources)),
                    sender + ' INIT missing receiver ACK')
        require(any(e['opcode_name'] == 'UPDATE' and 'EOT' in e['flag_names']
                    for e in packets), sender + ' EOT UPDATE missing')
    return {'events': len([e for e in events if e['family'] == family])}


def mismatch(events, family, sources, *, changed='ce'):
    by_ce = [e for e in by_role(events, family, 'ce', sources) if
             e['opcode_name'] == 'HELLO' and e['destination'] == MULTICAST[family]]
    by_oe = [e for e in by_role(events, family, 'oe', sources) if
             e['opcode_name'] == 'HELLO' and e['destination'] == MULTICAST[family]]
    def k1(packets, value):
        return any(t.get('k_values', [None])[0] == value for e in packets
                   for t in e['tlvs'] if t['type'] == 1)
    require(k1(by_ce, 2 if changed == 'ce' else 1),
            'Cisco HELLO K1 does not reflect configured mismatch')
    require(k1(by_oe, 2 if changed == 'oe' else 1),
            'OE HELLO K1 does not reflect configured mismatch')
    return {'ce_k1': 2 if changed == 'ce' else 1,
            'oe_k1': 2 if changed == 'oe' else 1}


def as_mismatch(events, family, sources):
    for sender, expected in (('oe',4473),('ce',4474)):
        require(any(e['opcode_name'] == 'HELLO' and e['asn'] == expected
                    and e['destination'] == MULTICAST[family]
                    for e in by_role(events, family, sender, sources)),
                f'{sender} did not transmit AS {expected} HELLO')
    return {'oe_as':4473, 'ce_as':4474}


def routes_in_wire(events, family, prefixes, sources):
    """Confirm each exact prefix was delivered on the OE/CE boundary."""
    out = {}
    for sender, routes in prefixes.items():
        packets = by_role(events, family, sender, sources)
        advertised = {prefix for e in packets for t in e['tlvs']
                      if t.get('route_kind') for prefix in t.get('prefixes', [])}
        absent = set(routes) - advertised
        require(not absent, f'{sender} did not advertise {sorted(absent)[:5]}')
        out[sender] = sorted(advertised.intersection(routes))
    return out


def codecs(events, family, prefixes, sources, required_type):
    routes_in_wire(events, family, prefixes, sources)
    codecs_seen = {}
    for sender in ('oe', 'ce'):
        types = sorted({t['type'] for e in by_role(events, family, sender, sources)
                        for t in e['tlvs'] if t.get('route_kind')})
        for kind in types:
            require(kind in wire.TLVS, 'unknown route codec cannot be qualified')
        # A route advertised using TLV1 must not count toward this scenario's
        # required TLV2 qualification, even if the prefix is otherwise correct.
        qualified = {p for e in by_role(events, family, sender, sources)
                     for t in e['tlvs'] if t['type'] == required_type
                     for p in t.get('prefixes', [])}
        missing = set(prefixes[sender]) - qualified
        require(not missing, f'{sender} missing TLV2 routes {sorted(missing)[:5]}')
        codecs_seen[sender] = {'types': types,
                               'codecs': sorted({'TLV2' if t in (0x0602,0x0603)
                                                 else 'TLV1' for t in types})}
    return codecs_seen


def packetization(events, family, prefixes, sources, *, startup_events=(), maximum=1500):
    routes_in_wire(events, family, prefixes, sources)
    details = {}
    for sender in ('oe', 'ce'):
        packets = [e for e in by_role(events, family, sender, sources)
                   if e['opcode_name'] == 'UPDATE' and
                   any(t.get('route_kind') for t in e['tlvs'])]
        # A simultaneous 128-prefix burst may legitimately fill every UPDATE.
        # Qualify single-TLV encoding with separately captured one-prefix
        # advertisements, and multi-TLV batching with the burst itself.
        single = [e for e in by_role(startup_events, family, sender, sources)
                  if e['opcode_name'] == 'UPDATE' and
                  sum(bool(t.get('route_kind')) for t in e['tlvs']) == 1]
        multiple = [e for e in packets if
                    sum(bool(t.get('route_kind')) for t in e['tlvs']) > 1]
        require(single, sender + ' has no single-TLV route UPDATE in startup capture')
        require(multiple, sender + ' has no multi-TLV route UPDATE in burst capture')
        require(len({e['sequence'] for e in packets if e['sequence']}) >= 2,
                sender + ' never split route burst into multiple RTP packets')
        for e in packets + single:
            # PCAP decoder already rejects truncated TLVs and IPv4 fragments.
            require(e['packet_length'] + (20 if family == 'ipv4' else 40) <= maximum,
                    sender + ' EIGRP packet exceeds configured IP MTU')
            require(e['ack'] == 0 if e['destination'] == MULTICAST[family] else True,
                    'multicast UPDATE carried ACK')
        details[sender] = {'single': len(single), 'multi': len(multiple),
                           'route_packets': len(packets)}
    return details


def reliability(events, family, sources):
    outcome = {}
    for sender in ('oe', 'ce'):
        receiver = 'ce' if sender == 'oe' else 'oe'
        sent = [e for e in by_role(events, family, sender, sources)
                if e['opcode_name'] in ('UPDATE','QUERY','REPLY') and e['sequence']]
        require(sent, sender + ' never sent a reliable packet')
        acknowledged = {e['ack'] for e in by_role(events, family, receiver, sources)
                        if e['ack'] and e['destination'] == sources[sender][family]}
        missing = set(e['sequence'] for e in sent) - acknowledged
        require(not missing, f'{sender} missing ACK for {sorted(missing)}')
        # New first transmissions must be monotonically ordered; same seq is a retry.
        firsts = list(dict.fromkeys(e['sequence'] for e in sent))
        require(all(b > a for a, b in zip(firsts, firsts[1:])),
                sender + ' reliable sequences are out of order')
        outcome[sender] = {'unique_sequences': len(firsts), 'acknowledged': len(acknowledged)}
    return outcome


def addressing(events, family, sources):
    outcome = {}
    for sender in ('oe', 'ce'):
        receiver = 'ce' if sender == 'oe' else 'oe'
        packets = by_role(events, family, sender, sources)
        require(any(e['opcode_name'] == 'HELLO' and e['sequence'] == 0 and
                    e['destination'] == MULTICAST[family] for e in packets),
                sender + ' multicast HELLO missing')
        require(any(e['opcode_name'] == 'UPDATE' and e['sequence'] and
                    e['destination'] == MULTICAST[family] for e in packets),
                sender + ' reliable multicast UPDATE missing')
        require(any(e['ack'] and e['sequence'] == 0 and
                    e['destination'] == sources[receiver][family] for e in packets),
                sender + ' unicast pure ACK missing')
        require(any(e['opcode_name'] == 'UPDATE' and e['sequence'] and
                    e['destination'] == sources[receiver][family] for e in packets),
                sender + ' reliable unicast UPDATE missing')
        outcome[sender] = 'multicast HELLO/UPDATE, unicast UPDATE/ACK observed'
    return outcome


def retry(events_tx, events_rx, family, sender, sources, prefix, *, lost_ack):
    receiver = 'ce' if sender == 'oe' else 'oe'
    tx = by_role(events_tx, family, sender, sources)
    rx = by_role(events_rx, family, sender, sources)
    tx_sequences = Counter(e['sequence'] for e in tx if e['opcode_name'] == 'UPDATE'
                           and e['sequence'] and any(prefix in t.get('prefixes', [])
                                                     for t in e['tlvs']))
    retried = {seq for seq, count in tx_sequences.items() if count >= 2}
    require(retried, sender + f' did not retransmit UPDATE for {prefix}')
    require(any(prefix in t.get('prefixes', []) for e in rx for t in e['tlvs']),
            f'receiver did not get {prefix} from retransmission')
    if lost_ack:
        require(any(sum(e['sequence'] == seq for e in rx) >= 2 for seq in retried),
                'receiver never saw duplicate UPDATE from lost-ACK case')
    else:
        # The active-fault gate already proves the original transmission was
        # not delivered. Multiple copies after restoration are legal RTP
        # retries, provided the peer installs only one route.
        require(any(e['sequence'] in retried for e in rx),
                'lost-packet case never delivered a retried UPDATE')
    require(any(e['ack'] in retried and e['destination'] == sources[sender][family]
                for e in by_role(events_tx, family, receiver, sources)),
            'final ACK did not reach original sender after fault restoration')
    return {'retried_sequences': sorted(retried), 'loss_case': 'ACK' if lost_ack else 'packet'}


def fault_drops(container, *, require_positive=True, direction='egress'):
    # The tc filter is specific to this test's isolated network namespace.
    # Verify that its action actually dropped traffic, rather than
    # interpreting an unrelated RTP retransmission as evidence of impairment.
    output = network_fault.ns_command(container,
                ['tc', '-s', 'filter', 'show', 'dev', 'eth1', direction])
    counters = [int(x) for x in re.findall(r'\(dropped\s+(\d+)', output)]
    require(counters, 'tc did not expose an action drop counter')
    if require_positive:
        require(sum(counters) > 0, 'configured protocol-88 fault dropped no packets')
    return sum(counters)


def fault_update_wait(tx_pcap, rx_pcap, family, sender, sources, prefix,
                      container, *, lost_ack, timeout=90):
    """Hold the impairment until its intended UPDATE and tc drop are real.

    A fixed delay can expire before FRR/Zebra emits the new UPDATE, yielding
    false retransmission failures. Tcpdump uses -U, so in-flight PCAPs can be
    inspected without stopping capture. Incomplete trailing records are
    expected while tcpdump is still writing; the final Trace validates them.
    """
    deadline = time.monotonic() + timeout
    last = 'target UPDATE not yet observed'
    while True:
        try:
            events, _ = wire.read_pcap(tx_pcap)
            sent = [e for e in by_role(events, family, sender, sources)
                    if e['opcode_name'] == 'UPDATE' and e['sequence'] and
                    any(prefix in t.get('prefixes', []) for t in e['tlvs'])]
            if sent:
                sequence = sent[0]['sequence']
                dropped = fault_drops(container, require_positive=False,
                                      direction='egress' if lost_ack else 'ingress')
                if dropped:
                    received, _ = wire.read_pcap(rx_pcap)
                    arrived = [e for e in received
                               if e['family'] == family and
                               e['source'] == sources[sender][family] and
                               e['opcode_name'] == 'UPDATE' and
                               e['sequence'] == sequence and
                               any(prefix in t.get('prefixes', []) for t in e['tlvs'])]
                    if not lost_ack:
                        # AF_PACKET may capture inbound traffic *before* tc
                        # ingress drops it. Seeing the frame on rx.pcap does
                        # not mean the receiver EIGRP process accepted it.
                        # Require the tc drop and no ACK for this sequence
                        # while the filter is still installed.
                        acknowledged = any(
                            e['family'] == family and
                            e['source'] == sources['ce' if sender == 'oe' else 'oe'][family]
                            and e['ack'] == sequence for e in received)
                        if acknowledged:
                            raise AssertionError('receiver ACKed target UPDATE while '
                                                 'packet-loss filter was active')
                    if lost_ack and not arrived:
                        last = 'ACK drop observed but original UPDATE absent at receiver'
                    else:
                        return sequence, dropped
                else:
                    last = 'target UPDATE observed, but tc dropped no packets'
        except (OSError, ValueError) as exc:
            # A live tcpdump file can have a partially written last record.
            last = f'capture not yet readable: {exc}'
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f'RTP fault not exercised: {last}')
        time.sleep(min(0.5, remaining))


def unique_external_rib(output, prefix, role, family):
    # Reuse the proven IOS/FRR RIB decoder for prefix identity and route kind.
    # Count actual entries separately: a set would hide duplicated CLI rows.
    # Descriptor ownership is not inferred from the observable RIB count.
    destination = ipaddress.ip_network(prefix, strict=True)
    found = destination in rib_prefixes(output, role, family, 'external')
    if role == 'oe':
        # FRR Zebra prints E[121], not Cisco's D EX spelling. The wire
        # assertions independently qualify external-route provenance.
        lead = r'^\s*E(?:\[\d+\])?[>*+]*\s+'
        address = re.escape(prefix)
    elif family == 'ipv4':
        # IOS XE can omit /length on D EX rows, with length in the preceding
        # ".../32 is subnetted" heading. rib_prefixes verifies that heading.
        lead = r'^\s*D[>*+]*\s+EX\s+'
        address = re.escape(str(destination.network_address)) + \
                  r'(?:/' + str(destination.prefixlen) + r')?'
    else:
        lead = r'^\s*EX\s+'
        address = re.escape(prefix)
    pattern = re.compile(lead + address + r'(?=\s|$)', re.I | re.M)
    count = len(pattern.findall(output)) if found else 0
    require(count == 1, f'expected exactly one EIGRP-external RIB entry {prefix}, saw {count}')
    return count


def inspect_queue(output):
    """Only claim queue drain if an actual CLI Q Cnt column was parsed."""
    lines = output.splitlines()
    # Cisco and FRR both wrap the heading as "Q  Seq" / "Cnt Num" in the
    # observed output. An unrelated trailing numeric pair is not Q Cnt proof.
    header = next((i for i, line in enumerate(lines)
                   if 'Q Cnt' in line or 'QCnt' in line or
                   (re.search(r'\bQ\s+Seq\b', line) and
                    i + 1 < len(lines) and
                    re.search(r'\bCnt\s+Num\b', lines[i+1]))), None)
    if header is None:
        raise AssertionError('neighbor output has no reliable Q Cnt evidence')
    # IOS XE IPv6 puts "Link-local address:" on the numeric neighbor row,
    # followed by a separate line containing the actual address.
    rows = [x for x in lines[header+1:] if
            re.search(r'10\.44\.53\.|Link-local address:', x, re.I) or
            (re.search(r'fe80:', x, re.I) and
             re.search(r'\s+\d+\s+\d+\s*$', x))]
    require(rows, 'no neighbor rows following Q Cnt heading')
    for row in rows:
        match = re.search(r'\s+(\d+)\s+(\d+)\s*$', row)
        if not match or int(match.group(1)) != 0:
            raise AssertionError('neighbor Q Cnt did not drain to zero')
    return len(rows)


def prefix(role, family, i):
    # Distinct reserved ranges per sender and AF. No route overlaps link or mgmt.
    if family == 'ipv4':
        return f'{"198.18" if role == "oe" else "198.19"}.{i // 256}.{i % 256}/32'
    return f'2001:db8:4473:{"100" if role == "oe" else "200"}::{i:x}/128'


def external_diagnostics(lab, family, label):
    """Capture both source and protocol state before failed-route cleanup."""
    for role, kind, cmd in (
        ('oe', 'config', 'show running-config'),
        ('oe', 'static', 'show ip route static' if family == 'ipv4'
         else 'show ipv6 route static'),
        ('oe', 'topology', f'show eigrp address-family {family} topology 4473 all-links'),
        ('oe', 'events', f'show eigrp address-family {family} 4473 events'),
        ('ce', 'config', 'show running-config'),
        ('ce', 'static', 'show ip route static' if family == 'ipv4'
         else 'show ipv6 route static'),
        ('ce', 'topology', f'show {"ip" if family == "ipv4" else "ipv6"} eigrp topology all-links'),
    ):
        try:
            output = lab.oe_cli(cmd) if role == 'oe' else lab.ce_cli(cmd)
        except Exception as exc:
            output = f'diagnostic command failed: {exc}'
        lab.save(f'{label}-{family}-{role}-{kind}.log', f'$ {cmd}\n{output}\n')
    try:
        output = command(['docker', 'exec', lab.oe, 'tail', '-n', '400',
                          '/var/log/frr/frr.log'], timeout=20)
    except Exception as exc:
        output = f'diagnostic command failed: {exc}'
    lab.save(f'{label}-{family}-oe-frr.log', output)


def sources_for(lab):
    addresses = {k: dict(v) for k, v in ORIGINS.items()}
    # The link-local from the *opposite* router is the sender address.
    addresses['oe']['ipv6'] = lab.peer_address('ce', 'ipv6')
    addresses['ce']['ipv6'] = lab.peer_address('oe', 'ipv6')
    return addresses


def new_case(report, case, family, action):
    key = f'{case}/{family}'
    try:
        report['objectives'][key] = result('PASS', **action())
    except Exception as exc:
        report['objectives'][key] = result('FAIL', error=str(exc))
    print(f"{report['objectives'][key]['status']} {key}", flush=True)
    return report['objectives'][key]['status'] == 'PASS'


def run(lab, report, spec):
    sources = sources_for(lab)
    families = spec['families']
    recorded = {}
    lab.snapshot('baseline')
    for family in families:
        def check_start():
            with Trace(lab, f'rtp01-{family}', target='ce') as trace:
                command(['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'eth1', 'down'])
                try:
                    lab.peers(family, False, 90, addresses=sources)
                finally:
                    command(['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'eth1', 'up'])
                lab.peers(family, True, 160)
                time.sleep(8)
            recorded[f'{family}-startup'] = trace.events
            return startup(trace.events, family, sources)
        new_case(report, 'RTP-01', family, check_start)

        def check_mismatch():
            # Change Cisco K-values *before* bringing up the transit link,
            # avoiding the all-255 goodbye HELLO sent on an established peer.
            # A goodbye is teardown evidence, not a K1=2 mismatch HELLO.
            # Starting tcpdump after OE's transit link is down is unsafe:
            # its veth peer in Cisco can also report IFF_UP=false. Begin the
            # capture while the link is still usable, then apply the stimulus.
            with Trace(lab, f'rtp02-{family}', target='ce') as trace:
                command(['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'eth1', 'down'])
                configured = False
                try:
                    lab.peers(family, False, 90, addresses=sources)
                    lab.cisco_af(family, 'metric weights 0 2 0 1 0 0 0')
                    configured = True
                    running = lab.ce_cli('show running-config')
                    require(re.search(r'(?m)^\s*metric weights 0 2 0 1 0 0 0\s*$',
                                      running),
                            'Cisco active configuration lacks intended K1=2')
                    command(['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'eth1', 'up'])
                    lab.peers(family, False, 45, addresses=sources)
                    time.sleep(13)  # observe at least two default HELLO intervals
                    lab.peers(family, False, 10, addresses=sources)
                finally:
                    # A Cisco rollback failure must not leave the OE link down.
                    try:
                        if configured:
                            lab.cisco_af(family, 'no metric weights')
                    finally:
                        command(['docker', 'exec', lab.oe, 'ip', 'link', 'set', 'eth1', 'up'])
                    lab.peers(family, True, 160)
            # Only mismatched-state packets can satisfy the K-value check.
            return mismatch(trace.events, family, sources)
        new_case(report, 'RTP-02', family, check_mismatch)

        def check_oe_k():
            commands = ['router eigrp OpenEIGRP',
                        f'address-family {family} unicast autonomous-system 4473']
            with Trace(lab, f'rtp02-oe-{family}') as trace:
                try:
                    lab.oe_config(commands + ['metric weights 0 2 0 1 0 0 0'])
                    lab.peers(family, False, 90, addresses=sources)
                    time.sleep(6)
                finally:
                    lab.oe_config(commands + ['no metric weights'])
                lab.peers(family, True, 160)
            return mismatch(trace.events, family, sources, changed='oe')
        new_case(report, 'RTP-02-K-oe', family, check_oe_k)

        def check_as():
            # Both implementations must transmit HELLOs with different ASNs;
            # shutting down an AF alone is not an AS-mismatch test.
            base = ['router eigrp OpenEIGRP']
            peer_af = f'address-family {family} unicast autonomous-system 4474'
            lines = base + [f'no address-family {family} unicast autonomous-system 4473',
                            peer_af, 'eigrp router-id 10.255.0.2']
            if family == 'ipv4':
                lines += ['network 10.44.53.2 0.0.0.0']
            lines += ['no shutdown', 'af-interface GigabitEthernet2',
                      'no passive-interface', 'no shutdown']
            try:
                with Trace(lab, f'rtp02-as-{family}') as trace:
                    lab.ce_config(lines)
                    lab.peers(family, False, 90, addresses=sources)
                    time.sleep(7)
            finally:
                lab.ce_config(base + [f'no {peer_af}'])
                # Restore only through the native provisioning authority.
                command(['netlab','initial'], cwd=lab.topology.parent, timeout=750)
                lab.peers(family, True, 160)
            return as_mismatch(trace.events, family, sources)
        new_case(report, 'RTP-02-AS', family, check_as)

        # Burst routes are a scenario stimulus, not a new router config path.
        # With the peer disconnected all prefixes are present when it starts
        # sending its next full UPDATE stream, permitting real packet batching.
        counts = spec['burst_prefixes']
        burst = {r: [prefix(r, family, i) for i in range(1, counts+1)]
                 for r in ('oe','ce')}
        def route_burst():
            added = {'oe': [], 'ce': []}
            try:
                with Trace(lab, f'burst-{family}', target='ce') as trace:
                    command(['docker','exec',lab.oe,'ip','link','set','eth1','down'])
                    try:
                        lab.peers(family, False, 90, addresses=sources)
                        for role in ('oe','ce'):
                            # Mark intended routes before mutation so a partial
                            # batch failure cannot skip rollback of a prefix.
                            added[role] = list(burst[role])
                            lab.routes_change(role, family, burst[role], add=True)
                    finally:
                        command(['docker','exec',lab.oe,'ip','link','set','eth1','up'])
                    lab.peers(family, True, 160)
                    try:
                        lab.check_routes(family, {'oe': burst['ce'], 'ce': burst['oe']},
                                         'external', seconds=240)
                    except AssertionError:
                        external_diagnostics(lab, family, 'burst')
                        raise
                    time.sleep(12)
                recorded[family] = {'events': trace.events, 'routes': burst}
                return {'prefixes_per_sender': counts}
            finally:
                # Always retire the burst routes, even when transport fails.
                for role in ('oe','ce'):
                    if added[role]:
                        lab.routes_change(role, family, added[role], add=False)
        if not new_case(report, 'RTP-BURST-SETUP', family, route_burst):
            for case in ('RTP-03','RTP-04','RTP-05','RTP-06','RTP-07','RTP-11'):
                report['objectives'][f'{case}/{family}'] = result('BLOCKED', reason='route burst failed')
        else:
            events, routes = recorded[family]['events'], recorded[family]['routes']
            def check_codec():
                init = recorded.get(f'{family}-startup', [])
                internal = {'oe':['10.255.0.1/32' if family == 'ipv4' else '2001:db8:4473:ff::1/128'],
                            'ce':['10.255.0.2/32' if family == 'ipv4' else '2001:db8:4473:ff::2/128']}
                routes_in_wire(init, family, internal, sources)
                for sender in ('oe','ce'):
                    assert any(t.get('route_kind') == 'internal'
                               for e in by_role(init, family, sender, sources)
                               for t in e['tlvs']), sender + ' internal TLVs missing'
                return {'internal': codecs(init, family, internal, sources, 0x0602),
                        'external': codecs(events, family, routes, sources, 0x0603)}
            new_case(report, 'RTP-03', family, check_codec)
            def check_packetization():
                # The adjacency initialization may legitimately batch several
                # internal TLVs into one EOT UPDATE. Exercise a single route
                # deliberately rather than requiring a coincidental one-TLV
                # startup packet. Retire the burst before the isolated probe.
                lab.check_routes(family, {'oe': routes['ce'], 'ce': routes['oe']},
                                 'external', present=False, seconds=120)
                singleton = []
                for sender in ('oe', 'ce'):
                    receiver = 'ce' if sender == 'oe' else 'oe'
                    p = prefix(sender, family, 998)
                    try:
                        with Trace(lab, f'rtp04-single-{sender}-{family}',
                                   target='ce') as trace:
                            lab.route(sender, family, p, add=True)
                            lab.check_routes(family, {receiver: [p]}, 'external',
                                             seconds=90)
                            time.sleep(2)
                        singleton.extend(trace.events)
                    finally:
                        lab.route(sender, family, p, add=False)
                    lab.check_routes(family, {receiver: [p]}, 'external',
                                     present=False, seconds=90)
                recorded[f'{family}-single'] = singleton
                return {'packetization': packetization(
                    events, family, routes, sources, startup_events=singleton)}
            new_case(report, 'RTP-04', family, check_packetization)
            def check_packet_boundaries():
                covered = routes_in_wire(events, family, routes, sources)
                packet_counts = {}
                for sender in ('oe','ce'):
                    seqs = {e['sequence'] for e in by_role(events, family, sender, sources)
                            if e['opcode_name'] == 'UPDATE' and e['sequence']
                            and any(t.get('route_kind') for t in e['tlvs'])}
                    require(len(seqs) >= 2, sender + ' route burst did not span packets')
                    packet_counts[sender] = len(seqs)
                return {'prefixes': covered, 'route_packets': packet_counts}
            new_case(report, 'RTP-05', family, check_packet_boundaries)
            new_case(report, 'RTP-06', family, lambda: {'addressing': addressing(
                recorded.get(f'{family}-startup', []) +
                recorded.get(f'{family}-single', []) + events,
                family, sources)})
            new_case(report, 'RTP-07', family, lambda: {'reliable': reliability(events, family, sources)})
            def pacing():
                lab.peers(family, True, 60)
                return {'ce_neighbors': inspect_queue(lab.neighbor('ce', family)),
                        'oe_neighbors': inspect_queue(lab.neighbor('oe', family)),
                        'reliable': reliability(events, family, sources)}
            new_case(report, 'RTP-11', family, pacing)

        for sender in ('oe', 'ce'):
            receiver = 'ce' if sender == 'oe' else 'oe'
            for ack_case in (False, True):
                tag = 'ack' if ack_case else 'packet'
                p = prefix(sender, family, 1000 if ack_case else 999)
                def test_loss():
                    # A prior fault must not silently contaminate this case.
                    # Confirm an established adjacency before introducing loss.
                    lab.peers(family, True, 90)
                    # An exact ACK selector must be based on a real preceding
                    # packet sequence. Never use a wildcard as proof of ACK loss.
                    ack = None
                    probe = prefix(sender, family, 997) if ack_case else None
                    if ack_case:
                        with Trace(lab, f'probe-{sender}-{family}') as t:
                            lab.route(sender, family, probe, add=True)
                            try:
                                lab.check_routes(family, {receiver:[probe]}, 'external', seconds=90)
                            except AssertionError:
                                external_diagnostics(lab, family, f'probe-{sender}')
                                raise
                            time.sleep(2)
                        sent = by_role(t.events, family, sender, sources)
                        sequences = [e['sequence'] for e in sent if e['opcode_name'] == 'UPDATE'
                                     and e['sequence']]
                        require(sequences, 'cannot predict next ACK sequence from live update')
                        ack = max(sequences) + 1
                        require(ack <= 0xffffffff, 'sequence rollover requires a separate test')
                    try:
                        with Trace(lab, f'loss-tx-{tag}-{sender}-{family}', target=sender) as tx, \
                             Trace(lab, f'loss-rx-{tag}-{sender}-{family}', target=receiver) as rx:
                            # Place packet loss at the receiver's ingress. A sender
                            # egress drop hides the UPDATE from sender tcpdump, so
                            # the fault gate can never see what it is impairing.
                            affected = receiver
                            with fault(lab, f'{tag}-{sender}-{family}',
                                       affected, family,
                                       'ack' if ack_case else 'drop', ack=ack,
                                       direction='egress' if ack_case else 'ingress'):
                                lab.route(sender, family, p, add=True)
                                try:
                                    observed, dropped = fault_update_wait(
                                        tx.pcap, rx.pcap, family, sender, sources, p,
                                        lab.oe if affected == 'oe' else lab.ce,
                                        lost_ack=ack_case, timeout=90)
                                except AssertionError:
                                    external_diagnostics(lab, family,
                                                         f'loss-{tag}-{sender}')
                                    raise
                                if not ack_case:
                                    require(not lab.route_present(receiver, family, p,
                                                                  'external'),
                                            'receiver installed target route while packet '
                                            'loss was active')
                                if ack_case:
                                    require(observed == ack,
                                            'exact ACK filter did not target route sequence')
                                # Keep the packet/ACK impaired long enough to
                                # exercise RTP retry, without dropping HELLOs.
                                time.sleep(2)
                            try:
                                lab.check_routes(family, {receiver:[p]}, 'external', seconds=100)
                            except AssertionError:
                                external_diagnostics(lab, family, f'loss-{tag}-{sender}')
                                raise
                            time.sleep(4)
                        require(lab.route_present(receiver, family, p, 'external'),
                                'route absent after retransmission')
                        unique_external_rib(lab.routes(receiver, family), p, receiver, family)
                        data = retry(tx.events, rx.events, family, sender,
                                     sources, p, lost_ack=ack_case)
                        if ack_case:
                            require(ack in data['retried_sequences'],
                                    'exact ACK filter targeted a different RTP sequence')
                        data['tc_dropped_packets'] = dropped
                        return data
                    finally:
                        lab.route(sender, family, p, add=False)
                        if probe is not None:
                            lab.route(sender, family, probe, add=False)
                new_case(report, f'RTP-08-{tag}-{sender}', family, test_loss)

    ack_subcases = [f'RTP-08-ack-{sender}/{family}' for sender in ('oe','ce')
                    for family in families]
    good = all(report['objectives'].get(case, {}).get('status') == 'PASS'
               for case in ack_subcases)
    report['objectives']['RTP-07/duplicate'] = result(
        'PASS' if good else 'BLOCKED',
        reason='duplicate UPDATE reception, final ACK and unique RIB entry '
               'require both ACK-loss directions in both address families')

    # A successful single-codec exchange is not TLV1 + TLV2 qualification.
    # Distinguish an unsupported/untested codec from a proven interoperability PASS.
    matrix = {'oe': {'internal': set(), 'external': set()},
              'ce': {'internal': set(), 'external': set()}}
    for family in families:
        combined = recorded.get(f'{family}-startup', []) + \
                   recorded.get(family, {}).get('events', [])
        for role in ('oe','ce'):
            for event in by_role(combined, family, role, sources):
                for tlv in event['tlvs']:
                    kind = tlv.get('route_kind')
                    if kind:
                        matrix[role][kind].add(tlv.get('codec',
                            'TLV2' if tlv['type'] in (0x0602,0x0603) else 'TLV1'))
    missing = [f'{role}/{kind}/{codec}' for role in ('oe','ce')
               for kind in ('internal','external') for codec in ('TLV1','TLV2')
               if codec not in matrix[role][kind]]
    # TLV1 is not exercised by this multiprotocol topology. Retain the
    # evidence, but qualify legacy codec coverage in a separate increment.
    report['objectives']['RTP-03/codec-matrix'] = result(
        'BLOCKED' if missing else 'PASS', required=False,
        observed={r:{k:sorted(v) for k,v in parts.items()} for r,parts in matrix.items()},
        missing=missing)
    print(f"{report['objectives']['RTP-03/codec-matrix']['status']} "
          'RTP-03/codec-matrix (informational; TLV1 deferred)', flush=True)
    lab.snapshot('final')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load(args.scenario.read_text())
    if (spec.get('schema') != 'openeigrp-live-rtp-2r/v1' or
        spec.get('name') != '2R-transport' or spec.get('group') != 'rtp' or
        spec.get('families') != ['ipv4', 'ipv6'] or
        spec.get('burst_prefixes', 0) < 96):
        parser.error('invalid 2R RTP fixture')
    lab = Lab(args.topology, args.artifacts)
    report = report_base(spec['name'], lab)
    required = [f'{case}/{af}' for case in CASES for af in spec['families']]
    required += [f'RTP-08-{tag}-{role}/{af}'
                 for tag in ('ack','packet') for role in ('oe','ce') for af in spec['families']]
    required += [f'RTP-02-{kind}/{af}' for kind in ('K-oe','AS') for af in spec['families']]
    # The combined TLV1/TLV2 matrix is reported but is not a requirement
    # of this TLV2-focused topology. RTP-03 per-AF remains required.
    required += ['RTP-07/duplicate']
    # RTP-08 aggregate ID derives from independently scored subcases.
    required = [x for x in required if not x.startswith('RTP-08/')]
    report['required'] = required
    try:
        require_packet_access()
        report['oe_image'] = lab.deploy()
        report['ce_image'] = lab.info['defaults']['devices.csr.clab.image']
        for af in spec['families']:
            lab.peers(af, True, 140)
        run(lab, report, spec)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        for obj in required:
            report['objectives'].setdefault(obj, result('BLOCKED', reason=str(exc)))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        lab.collect_logs()
        for obj in required:
            report['objectives'].setdefault(obj, result('NOT_RUN'))
        statuses = [report['objectives'][x]['status'] for x in required]
        report['status'] = ('PASS' if all(x == 'PASS' for x in statuses)
                            else 'BLOCKED' if all(x == 'BLOCKED' for x in statuses)
                            else 'FAIL')
        write_report(args.artifacts, report)
    print(report['status'], '2R RTP', flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
