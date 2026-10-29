#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Live OE/CE internal/external routes, lifecycle, and one-path metrics."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
from interop import Lab, Trace, report_base, result, write_report
from interop import CapturePrerequisiteError, require_packet_access

PREFIX = {
    'internal': {
        'oe': {'ipv4': '10.255.0.1/32', 'ipv6': '2001:db8:4473:ff::1/128'},
        'ce': {'ipv4': '10.255.0.2/32', 'ipv6': '2001:db8:4473:ff::2/128'},
    },
    'external': {
        'oe': {'ipv4': '198.18.255.1/32', 'ipv6': '2001:db8:4473:100::ff/128'},
        'ce': {'ipv4': '198.19.255.2/32', 'ipv6': '2001:db8:4473:200::ff/128'},
    },
}


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def route_tlvs(events, family, sender, prefix, kind, sources):
    """Find exact advertised prefix and route type, not a generic TLV hit."""
    return [(e, t) for e in events
            if e['family'] == family and e['source'] == sources[sender][family]
            and e['opcode_name'] == 'UPDATE'
            for t in e['tlvs'] if t.get('route_kind') == kind
            and prefix in t.get('prefixes', [])]


def route_wire(events, family, kind, routes, sources):
    out = {}
    for sender, prefix in routes.items():
        matched = route_tlvs(events, family, sender, prefix, kind, sources)
        require(matched, f'{sender} did not advertise {kind} {prefix} over OE/CE link')
        for event, tlv in matched:
            require(event['sequence'] != 0, f'{sender} unsequenced {kind} UPDATE')
            require(event['asn'] == 4473, f'{sender} wrong autonomous system')
            if 'next_hop' in tlv:
                next_hop = ipaddress.ip_address(tlv['next_hop'])
                require(not next_hop.is_multicast, 'invalid multicast next hop')
            metric = tlv.get('metric', {})
            require(metric.get('bandwidth', 0) > 0 and metric.get('mtu', 0) > 0,
                    f'{sender} missing advertised metric attributes')
            require('delay' in metric and 'reliability' in metric,
                    f'{sender} incomplete route metric')
        out[sender] = {'prefix': prefix,
                       'types': sorted({t['type'] for _, t in matched}),
                       'metrics': [t['metric'] for _, t in matched]}
    return out


def provenance(events, family, routes, sources):
    seen = route_wire(events, family, 'external', routes, sources)
    for sender, prefix in routes.items():
        items = route_tlvs(events, family, sender, prefix, 'external', sources)
        for _, tlv in items:
            ext = tlv.get('external')
            require(ext is not None, f'{sender} external provenance absent')
            require(all(k in ext for k in ('origin','as','protocol','metric')),
                    f'{sender} external provenance fields missing')
            if 'tag' in ext:
                require(ext['tag'] == 7, f'{sender} external tag corrupted: {ext["tag"]}')
            else:
                require(tlv.get('metric', {}).get('tag') == 7,
                        f'{sender} MP external route tag missing or corrupted')
        seen[sender]['provenance'] = [t['external'] for _,t in items]
    return seen


def route_rib(lab, family, kind, routes, *, present=True):
    learned = {'oe':[routes['ce']], 'ce':[routes['oe']]}
    return lab.check_routes(family, learned, kind, present=present, seconds=120)


# IOS XE may print IPv6 prefixes in uppercase and may choose a different
# abbreviation for the same IPv6 network. Compare parsed network identities.
PREFIX_TOKEN = re.compile(r'(?<![0-9a-fA-F:.])([0-9a-fA-F:.]+/\d{1,3})(?![0-9a-fA-F:.])')


def line_has_prefix(line, prefix):
    expected = ipaddress.ip_network(prefix)
    for match in PREFIX_TOKEN.finditer(line):
        try:
            if ipaddress.ip_network(match.group(1)) == expected:
                return True
        except ValueError:
            continue
    return False


def actual_next_hop(output, family, prefix, neighbor):
    """Require a matching prefix and next hop within the same RIB entry."""
    lines = output.splitlines()
    expected = ipaddress.ip_address(neighbor)
    route_start = re.compile(r'^\s*(?:D\b|EX\b|E(?:\[\d+\])?)')
    for idx, line in enumerate(lines):
        if not line_has_prefix(line, prefix) or not route_start.match(line):
            continue
        entry = [line]
        for following in lines[idx+1:idx+4]:
            if route_start.match(following):
                break
            entry.append(following)
        block = '\n'.join(entry)
        for match in re.finditer(r'\bvia\s+([0-9a-f:.]+)', block, re.I):
            try:
                if ipaddress.ip_address(match.group(1)) == expected:
                    return block
            except ValueError:
                continue
    raise AssertionError(f'RIB {prefix} has no next hop {neighbor}')


def numeric_metric(block):
    """Extract destination FD and via(CD/RD) from real topology text."""
    fd = re.search(r'\bFD\s+is\s+(\d+)', block, re.I)
    pair = re.search(r'\(\s*(\d+)\s*/\s*(\d+)\s*\)', block)
    if not fd or not pair:
        raise AssertionError('topology lacks machine-verifiable FD/CD/RD')
    return {'fd': int(fd.group(1)), 'cd': int(pair.group(1)),
            'rd': int(pair.group(2))}


def topology_metrics(lab, family, prefix, role):
    if role == 'ce':
        cmd = f'show {"ip" if family == "ipv4" else "ipv6"} eigrp topology all-links'
        output = lab.ce_cli(cmd)
    else:
        output = lab.oe_cli(f'show eigrp address-family {family} topology 4473 all-links')
    lines = output.splitlines()
    for i, line in enumerate(lines):
        if line_has_prefix(line, prefix):
            block = '\n'.join(lines[i:i+7])
            metric = numeric_metric(block)
            # IOS XE retains the feasible distance at its historical minimum
            # while the current computed distance rises after a metric change.
            require(metric['fd'] <= metric['cd'],
                    f'{role} feasible distance exceeds computed distance')
            require(metric['rd'] < metric['cd'],
                    f'{role} computed distance must include positive local-link cost')
            return metric
    raise AssertionError(f'{role} topology missing {prefix}')



def require_distance_change(before, after):
    # FD is a historical feasibility bound, not the current computed metric.
    require(after['cd'] != before['cd'], 'remote topology computed distance unchanged')
    require(after['rd'] != before['rd'], 'remote topology reported distance unchanged')


def external_diagnostics(lab, family, label):
    """Save provenance at the point of failure, before static cleanup."""
    commands = (
        ('oe', 'configuration', 'show running-config'),
        ('oe', 'rib', 'show ipv6 route' if family == 'ipv6' else 'show ip route'),
        ('oe', 'topology', f'show eigrp address-family {family} topology 4473 all-links'),
        ('oe', 'events', f'show eigrp address-family {family} 4473 events'),
        ('ce', 'configuration', 'show running-config'),
        ('ce', 'rib', f'show {"ipv6" if family == "ipv6" else "ip"} route'),
        ('ce', 'topology', f'show {"ipv6" if family == "ipv6" else "ip"} eigrp topology all-links'),
    )
    for role, kind, cmd in commands:
        try:
            output = lab.oe_cli(cmd) if role == 'oe' else lab.ce_cli(cmd)
        except Exception as exc:
            output = f'diagnostic command failed: {exc}'
        lab.save(f'{label}-{family}-{role}-{kind}.log', f'$ {cmd}\n{output}\n')
    # The container's docker log includes only supervisor messages in this lab.
    # The FRR file log records failed Zebra redistribution ingress results.
    node = getattr(lab, 'oe', None)
    if node:
        from interop import command
        args = ['docker', 'exec', node, 'tail', '-n', '400', '/var/log/frr/frr.log']
        try:
            output = command(args, timeout=20)
        except Exception as exc:
            output = f'diagnostic command failed: {exc}'
        lab.save(f'{label}-{family}-oe-frr.log', '$ ' + ' '.join(args) + '\n' + output + '\n')


def change_metric(lab, family, role, *, changed):
    value = '100' if changed else '10'
    line = f'default-metric 100000 {value} 255 1 1500'
    base = ['router eigrp OpenEIGRP',
            f'address-family {family} unicast autonomous-system 4473',
            'topology base', line]
    if role == 'ce':
        lab.ce_config(base)
    else:
        lab.oe_config(base)


def unreachable(metric):
    return metric.get('delay') in (0xffffffff, 0xffffffffffff)


def lifecycle_evidence(events, family, sender, prefix, sources):
    # Cisco may initiate DUAL ACTIVE by querying with an unreachable metric
    # rather than emitting a poisoned UPDATE. The caller also verifies peer
    # RIB removal and restoration around this captured sequence.
    route = [(event, tlv) for event in events
             if event['family'] == family
             and event['source'] == sources[sender][family]
             and event['opcode_name'] in ('UPDATE', 'QUERY')
             and event['asn'] == 4473 and event['sequence'] != 0
             for tlv in event['tlvs']
             if tlv.get('route_kind') == 'external'
             and prefix in tlv.get('prefixes', [])]
    reachable = [i for i, (event, tlv) in enumerate(route)
                 if event['opcode_name'] == 'UPDATE'
                 and not unreachable(tlv['metric'])]
    withdrawn = [i for i, (_, tlv) in enumerate(route)
                 if unreachable(tlv['metric'])]
    require(withdrawn, 'route withdrawal lacks unreachable UPDATE/QUERY')
    first = withdrawn[0]
    require(any(i < first for i in reachable),
            'route was withdrawn without a preceding advertisement')
    require(any(i > first for i in reachable),
            'route not re-advertised after withdrawal')
    return {'advertisements': len(reachable),
            'poisoned': len(withdrawn),
            'withdrawal_opcodes': sorted({route[i][0]['opcode_name']
                                          for i in withdrawn})}


def verify_change(before, after, family, sender, prefix, sources):
    old = route_tlvs(before, family, sender, prefix, 'external', sources)
    new = route_tlvs(after, family, sender, prefix, 'external', sources)
    require(old and new, 'metric-change missing before/after route advertisement')
    old_values = {(t['metric']['delay'], t['metric']['bandwidth']) for _,t in old}
    new_values = {(t['metric']['delay'], t['metric']['bandwidth']) for _,t in new}
    require(old_values != new_values, f'{sender} did not change advertised metric')
    return {'before': sorted(old_values), 'after': sorted(new_values)}


def record(report, case, family, fn):
    key = f'{case}/{family}'
    try:
        report['objectives'][key] = result('PASS', **fn())
    except Exception as exc:
        report['objectives'][key] = result('FAIL', error=str(exc))
    print(f"{report['objectives'][key]['status']} {key}", flush=True)


def sources_for(lab):
    return {'oe': {'ipv4':'10.44.53.1', 'ipv6':lab.peer_address('ce','ipv6')},
            'ce': {'ipv4':'10.44.53.2', 'ipv6':lab.peer_address('oe','ipv6')}}


def run(lab, report, spec):
    sources = sources_for(lab)
    lab.snapshot('baseline')
    for family in spec['families']:
        internal = {r: PREFIX['internal'][r][family] for r in ('oe', 'ce')}
        external = {r: PREFIX['external'][r][family] for r in ('oe', 'ce')}

        def check_internal():
            # Capture before resetting adjacency to force the full route exchange.
            with Trace(lab, f'internal-{family}', target='ce') as capture:
                lab.peers(family, True)
                # A controlled peer reset (not a new initial configuration).
                from interop import command
                command(['docker','exec',lab.oe,'ip','link','set','eth1','down'])
                try:
                    lab.peers(family, False, 90, addresses=sources)
                finally:
                    command(['docker','exec',lab.oe,'ip','link','set','eth1','up'])
                lab.peers(family, True, 160)
                route_rib(lab, family, 'internal', internal)
                time.sleep(5)
            messages = route_wire(capture.events, family, 'internal', internal, sources)
            next_hops = {}
            for receiver in ('oe','ce'):
                sender = 'ce' if receiver == 'oe' else 'oe'
                via = sources[sender][family]
                rib = lab.routes(receiver, family)
                next_hops[receiver] = actual_next_hop(rib, family, internal[sender], via)
            return {'wire': messages, 'next_hops': next_hops}
        record(report, 'DUAL-01', family, check_internal)

        def check_external():
            added = set()
            try:
                with Trace(lab, f'external-{family}') as capture:
                    for role in ('oe','ce'):
                        lab.route(role, family, external[role], add=True)
                        added.add(role)
                    route_rib(lab, family, 'external', external)
                    time.sleep(7)
                return {'wire': provenance(capture.events, family, external, sources),
                        'rib': {r: lab.routes(r, family) for r in ('oe','ce')}}
            except Exception:
                external_diagnostics(lab, family, 'dual02-failure')
                raise
            finally:
                for role in added:
                    lab.route(role, family, external[role], add=False)
        record(report, 'DUAL-02', family, check_external)

        def check_lifecycle():
            out = {}
            for role in ('oe','ce'):
                other = 'ce' if role == 'oe' else 'oe'
                route = external[role]
                inserted = False
                try:
                    with Trace(lab, f'lifecycle-{role}-{family}') as capture:
                        lab.route(role, family, route, add=True)
                        inserted = True
                        lab.check_routes(family, {other:[route]}, 'external')
                        time.sleep(3)
                        before = lab.routes(other, family)
                        before_distance = topology_metrics(lab, family, route, other)
                        try:
                            with Trace(lab, f'lifecycle-metric-{role}-{family}') as mt:
                                change_metric(lab, family, role, changed=True)
                                time.sleep(12)
                                after_distance = topology_metrics(lab, family, route, other)
                                # Capture the live configuration and topology
                                # while the changed seed metric is still set.
                                external_diagnostics(lab, family, f'dual03-metric-{role}')
                        finally:
                            change_metric(lab, family, role, changed=False)
                        lab.route(role, family, route, add=False)
                        inserted = False
                        lab.check_routes(family, {other:[route]}, 'external', present=False)
                        lab.route(role, family, route, add=True)
                        inserted = True
                        lab.check_routes(family, {other:[route]}, 'external')
                        time.sleep(6)
                    evidence = lifecycle_evidence(capture.events, family, role, route, sources)
                    initial = [e for e,t in route_tlvs(capture.events, family, role,
                               route, 'external', sources) if not unreachable(t['metric'])]
                    require(initial, 'initial reachable update absent')
                    baseline = [(e,t) for e,t in route_tlvs(capture.events, family,
                                role, route, 'external', sources)
                                if not unreachable(t['metric'])]
                    changed = [(e,t) for e,t in route_tlvs(mt.events, family, role,
                                route, 'external', sources)
                                if not unreachable(t['metric'])]
                    require(changed, 'metric update did not advertise a reachable route')
                    old_metric = baseline[0][1]['metric']
                    require(any((t['metric']['delay'],t['metric']['bandwidth']) !=
                                (old_metric['delay'],old_metric['bandwidth'])
                                for _,t in changed), 'metric-change UPDATE unchanged')
                    require(before != '', 'baseline RIB missing')
                    require_distance_change(before_distance, after_distance)
                    out[role] = {'lifecycle': evidence,
                                 'before_distance': before_distance,
                                 'after_distance': after_distance,
                                 'changed_metrics': [t['metric'] for _,t in changed]}
                except Exception:
                    external_diagnostics(lab, family, f'dual03-failure-{role}')
                    raise
                finally:
                    if inserted:
                        lab.route(role, family, route, add=False)
                    # Required after every stimulus, not only at final teardown.
                    lab.check_routes(family, {other:[route]}, 'external', present=False)
            return out
        record(report, 'DUAL-03', family, check_lifecycle)

        def check_metrics():
            data = {}
            for receiver in ('oe','ce'):
                sender = 'ce' if receiver == 'oe' else 'oe'
                lab.check_routes(family, {receiver:[internal[sender]]}, 'internal')
                data[receiver] = topology_metrics(lab, family, internal[sender], receiver)
            require(data['oe']['fd'] > 0 and data['ce']['fd'] > 0,
                    'zero feasible distance')
            return {'single_path_distances': data,
                    'scope': '2R RD/CD/FD only; alternate feasible successor requires 4R'}
        record(report, 'DUAL-04', family, check_metrics)
    lab.snapshot('final')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', required=True, type=Path)
    parser.add_argument('--topology', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load(args.scenario.read_text())
    if (spec.get('schema') != 'openeigrp-live-dual-2r/v1' or
        spec.get('name') != '2R-routes' or spec.get('group') != 'dual' or
        spec.get('families') != ['ipv4','ipv6'] or
        spec.get('asn') != 4473):
        parser.error('invalid 2R DUAL fixture')
    lab = Lab(args.topology, args.artifacts)
    report = report_base(spec['name'], lab)
    keys = [f'DUAL-0{i}/{af}' for i in range(1,5) for af in spec['families']]
    report['required'] = keys
    try:
        require_packet_access()
        report['oe_image'] = lab.deploy()
        report['ce_image'] = lab.info['defaults']['devices.csr.clab.image']
        for af in spec['families']:
            lab.peers(af, True, 140)
        run(lab, report, spec)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        for key in keys:
            report['objectives'].setdefault(key, result('BLOCKED', reason=str(exc)))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        lab.collect_logs()
        for key in keys:
            report['objectives'].setdefault(key, result('NOT_RUN'))
        statuses = [report['objectives'][key]['status'] for key in keys]
        report['status'] = ('PASS' if all(x == 'PASS' for x in statuses)
                            else 'BLOCKED' if all(x == 'BLOCKED' for x in statuses)
                            else 'FAIL')
        write_report(args.artifacts, report)
    print(report['status'], '2R DUAL', flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
