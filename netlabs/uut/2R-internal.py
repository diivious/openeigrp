#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Qualify native IPv4/IPv6 EIGRP internal routes in both Cisco/OE directions.

The proven 2R-neighbor driver owns the shared CLI/readiness operations. This
scenario adds independently verified remote prefixes, originated on passive
stub interfaces and installed as EIGRP-internal entries in each router's RIB.
"""
import argparse
import datetime as dt
import ipaddress
import json
from pathlib import Path
import re
import runpy
import shutil
import subprocess
import sys
import time

import yaml

# The remote client stages the established neighbor driver with this scenario.
neighbor = runpy.run_path(str(Path(__file__).with_name('2R-neighbor.py')))
command = neighbor['command']
assert_cli = neighbor['assert_cli']
Cisco = neighbor['Cisco']
wait_csr = neighbor['wait_csr']
wait_frr = neighbor['wait_frr']
validate_frr = neighbor['validate_frr']
get_csr_mgmt_ip = neighbor['get_csr_mgmt_ip']
assess_family = neighbor['assess_family']

# Shared wire capture is diagnostic only for this preexisting RIB qualification.
# Missing host permissions do not turn a missing route into PASS.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
from interop import (CapturePrerequisiteError, Trace, require_packet_access)
from types import SimpleNamespace

FRR = 'clab-eigrp-internal-oe'
CSR = 'clab-eigrp-internal-csr'
CISCO_IMAGE = 'vrnetlab/vr-csr:17.03.06'
# IOS XE: 'D 10.0.0.0/24', never 'D EX'. OE FRR 10.7 Zebra: 'E[121]'
# for EIGRP routes. A connected/local source is not proof of a learned route.
CISCO_INTERNAL = re.compile(r'^\s*D[>*+]*\s+([0-9a-fA-F:.]+/\d{1,3})(?=\s|$)', re.M)
FRR_INTERNAL = re.compile(r'^\s*E(?:\[\d+\])?[>*+]*\s+([0-9a-fA-F:.]+/\d{1,3})(?=\s|$)', re.M)


def internal_route_present(output, prefix, role='csr'):
    """Require an exact EIGRP RIB prefix, not a header or local route text."""
    target = ipaddress.ip_network(prefix, strict=True)
    regex = FRR_INTERNAL if role == 'oe' else CISCO_INTERNAL
    for line in output.splitlines():
        match = regex.match(line)
        if match and 'is directly connected' not in line:
            try:
                if ipaddress.ip_network(match.group(1), strict=True) == target:
                    return True
            except ValueError:
                continue
    return False


def route_tables(family, csr_ip, artifacts):
    verb = 'ip' if family == 'ipv4' else 'ipv6'
    # The tested OE image rejects 'show ipv6 route eigrp'. Its Zebra RIB
    # supports 'show ipv6 route', from which EIGRP rows are selected below.
    frr_cmd = 'show ipv6 route' if family == 'ipv6' else 'show ip route eigrp'
    cisco_cmd = f'show {verb} route eigrp'
    frr_output = command(['docker', 'exec', FRR, 'vtysh', '-c', frr_cmd], timeout=20)
    assert_cli(frr_output, f'FRR {frr_cmd}')
    with Cisco(csr_ip, artifacts / 'cisco-cli.log') as router:
        cisco_output = router.run(cisco_cmd)
    for target, cmd, output in [('frr', frr_cmd, frr_output),
                                ('csr', cisco_cmd, cisco_output)]:
        with (artifacts / f'{target}-{family}-routes.log').open('a', encoding='utf8') as log:
            log.write(f'\n$ {cmd}\n{output}\n')
    return frr_output, cisco_output


def route_diagnostics(family, csr_ip, artifacts):
    """Retain both topology tables after a missing learned prefix.

    This distinguishes an advertisement/DUAL failure from a Zebra/RIB issue.
    Missing diagnostic CLI support must not conceal the original route failure.
    """
    for role, cmd in [('oe', 'show ip eigrp topology 4473 all-links' if family == 'ipv4'
                       else None),
                      ('csr', f'show {"ip" if family == "ipv4" else "ipv6"} eigrp topology all-links')]:
        if cmd is None:
            (artifacts / f'{role}-{family}-topology.log').write_text(
                'DIAGNOSTIC NOT AVAILABLE: FRR eigrp_vty exposes IPv4 '
                'topology CLI, not an equivalent IPv6 topology command.\n', encoding='utf8')
            continue
        try:
            if role == 'oe':
                text = command(['docker', 'exec', FRR, 'vtysh', '-d', 'eigrpd', '-c', cmd], timeout=25)
                assert_cli(text, cmd)
            else:
                with Cisco(csr_ip, artifacts / 'cisco-cli.log') as router:
                    text = router.run(cmd)
        except Exception as exc:
            text = f'DIAGNOSTIC UNAVAILABLE: {exc}\n'
        (artifacts / f'{role}-{family}-topology.log').write_text(f'$ {cmd}\n{text}\n', encoding='utf8')


def assess_internal(family, csr_ip, artifacts, prefix_frr, prefix_csr, seconds):
    """Verify Cisco learned the OE prefix and OE learned Cisco's prefix."""
    deadline = time.monotonic() + seconds
    last_frr = last_cisco = ''
    while time.monotonic() < deadline:
        last_frr, last_cisco = route_tables(family, csr_ip, artifacts)
        received_by_frr = internal_route_present(last_frr, prefix_csr, role='oe')
        received_by_csr = internal_route_present(last_cisco, prefix_frr, role='csr')
        if received_by_frr and received_by_csr:
            return {'status': 'PASS', 'oe_learned': prefix_csr,
                    'cisco_learned': prefix_frr}
        time.sleep(min(6, max(0, deadline - time.monotonic())))
    route_diagnostics(family, csr_ip, artifacts)
    return {'status': 'FAIL', 'error': 'EIGRP-internal route absent from one or both RIBs',
            'oe_expected': prefix_csr, 'cisco_expected': prefix_frr,
            'oe_rib': last_frr, 'cisco_rib': last_cisco}


def internal_wire_evidence(events, routes, families, sources):
    """Require each router's own reachable prefix in its transmitted UPDATE.

    Learning or poisoning the other router's prefix is not proof of local
    origination. The source addresses come from the observed transport link,
    including the dynamically determined IPv6 link-local addresses.
    """
    expected = {(af, role): routes[role][af]
                for af in families for role in ('oe', 'csr')}
    observed = {key: False for key in expected}
    updates = {af: 0 for af in families}
    for event in events:
        af = event['family']
        if af not in updates or event['opcode_name'] != 'UPDATE':
            continue
        updates[af] += 1
        for role in ('oe', 'csr'):
            if event['source'].lower() != sources[role][af].lower():
                continue
            for tlv in event['tlvs']:
                if (tlv.get('route_kind') == 'internal'
                        and expected[(af, role)] in tlv.get('prefixes', [])
                        and tlv.get('metric', {}).get('delay', 0) != 0xffffffffffff):
                    observed[(af, role)] = True
    missing = {af: {role: expected[(af, role)]
                    for role in ('oe', 'csr') if not observed[(af, role)]}
               for af in families}
    return {'status': 'FAIL' if any(missing.values()) else 'PASS',
            'capture': 'internal-reinitialize.pcap',
            'packets': len(events), 'updates': updates,
            'missing_originated_internal_prefixes': missing}


def capture_context(artifacts):
    """Supply the complete Trace lab contract for the legacy UUT driver."""
    return SimpleNamespace(artifacts=artifacts, oe=FRR, ce=CSR,
        save=lambda name, content: (artifacts / name).write_text(
            content, encoding='utf8'))


def ipv6_origin_diagnostics(artifacts, asn):
    """Record the FRR host/EIGRP views without masking the route failure."""
    commands = {
        'oe-ipv6-loopback-addresses.log':
            ['docker', 'exec', FRR, 'ip', '-6', '-d', 'addr', 'show', 'dev', 'lo'],
        'oe-ipv6-transit-addresses.log':
            ['docker', 'exec', FRR, 'ip', '-6', '-d', 'addr', 'show', 'dev', 'eth1'],
        'oe-ipv6-eigrp-interfaces.log':
            ['docker', 'exec', FRR, 'vtysh', '-d', 'eigrpd', '-c',
             f'show eigrp address-family ipv6 {asn} interfaces'],
        'oe-ipv6-frr-interfaces.log':
            ['docker', 'exec', FRR, 'vtysh', '-c', 'show interface lo'],
    }
    for name, argv in commands.items():
        try:
            output = command(argv, timeout=20, check=False)
        except Exception as exc:
            output = f'DIAGNOSTIC UNAVAILABLE: {exc}\n'
        (artifacts / name).write_text(output, encoding='utf8')

def ipv6_passive_runtime(artifacts, interface):
    """Check the *runtime* interface table, not retained configuration."""
    output = (artifacts / 'oe-ipv6-eigrp-interfaces.log').read_text(encoding='utf8')
    matched = bool(re.search(r'(?m)^' + re.escape(interface) + r'\s+runtime\s', output))
    return {'status': 'PASS' if matched else 'FAIL', 'interface': interface,
            'reason': None if matched else 'configured passive interface has no EIGRP runtime'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', required=True, type=Path)
    parser.add_argument('--topology', required=True, type=Path)
    parser.add_argument('--artifacts', required=True, type=Path)
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    spec = yaml.safe_load(args.scenario.read_text(encoding='utf8'))
    if (spec.get('schema') != 'openeigrp-live-internal/v1' or
            spec.get('name') != '2R-internal' or
            spec.get('process') != 'OpenEIGRP' or spec.get('asn') != 4473 or
            spec.get('families') != ['ipv4', 'ipv6']):
        parser.error('unsupported internal-route scenario')
    routes = spec['routes']
    topology = yaml.safe_load(args.topology.read_text(encoding='utf8'))
    if topology.get('name') != 'eigrp-internal' or set(topology.get('nodes', {})) != {'oe', 'csr'}:
        parser.error('unexpected native netlab internal-route topology')
    report = {'scenario': spec['name'],
              'timestamp_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'ipv4': {'status': 'NOT_RUN'}, 'ipv6': {'status': 'NOT_RUN'}}
    rc = 1
    try:
        image = topology['defaults']['devices.frr.clab.image']
        if not (re.fullmatch(r'ghcr\.io/diivious/openeigrp-frr:[0-9]+\.[0-9]+\.[0-9]+', image)
                or re.fullmatch(r'openeigrp:[a-zA-Z0-9][a-zA-Z0-9_.-]*', image)):
            raise RuntimeError(f'Unexpected OpenEIGRP image reference: {image}')
        report['openeigrp_image'] = image
        for image in (image, CISCO_IMAGE):
            command(['docker', 'image', 'inspect', image], timeout=15)
        if not shutil.which('sshpass'):
            raise RuntimeError('sshpass is required for Cisco management access')
        print('Deploying and configuring OpenEIGRP and Cisco via netlab...', flush=True)
        import os
        previous = os.getcwd()
        try:
            os.chdir(args.topology.parent)
            command(['netlab', 'up'], timeout=800, log_file=args.artifacts / 'deploy.log')
        finally:
            os.chdir(previous)
        print('Checking named-mode configuration and neighbor readiness...', flush=True)
        wait_frr(FRR)
        frr_ll = validate_frr(FRR, args.artifacts, spec['process'], spec['asn'])
        csr_ip = get_csr_mgmt_ip(CSR)
        print('Checking Cisco IOS XE readiness (SSH retries logged)...', flush=True)
        csr_ll = wait_csr(csr_ip, args.artifacts, time.monotonic() + 420,
                          spec['process'], spec['asn'])
        ipv6_origin_diagnostics(args.artifacts, spec['asn'])
        if spec.get('require_ipv6_passive_runtime'):
            report['ipv6_passive_runtime'] = ipv6_passive_runtime(
                args.artifacts, spec['require_ipv6_passive_runtime'])
        # Force one observable adjacency reinitialization while capturing on
        # the OE-to-CE link. Passive RIB snapshots alone cannot distinguish a
        # missing UPDATE from a route accepted but not installed in the RIB.
        try:
            require_packet_access()
            capture_lab = capture_context(args.artifacts)
            with Trace(capture_lab, 'internal-reinitialize') as trace:
                command(['docker', 'exec', FRR, 'ip', 'link', 'set', 'eth1', 'down'])
                try:
                    time.sleep(3)
                finally:
                    command(['docker', 'exec', FRR, 'ip', 'link', 'set', 'eth1', 'up'])
                for af in spec['families']:
                    peers = assess_family(af, FRR, csr_ip, frr_ll, csr_ll,
                                          spec['asn'], args.artifacts,
                                          spec['neighbor_timeout'])
                    if peers['status'] != 'PASS':
                        raise RuntimeError(f'{af} did not recover after capture-triggered flap')
                time.sleep(8)
            report['wire_evidence'] = internal_wire_evidence(
                trace.events, routes, spec['families'],
                {'oe': {'ipv4': '10.44.53.1', 'ipv6': frr_ll},
                 'csr': {'ipv4': '10.44.53.2', 'ipv6': csr_ll}})

        except CapturePrerequisiteError as exc:
            report['wire_evidence'] = {'status': 'BLOCKED', 'reason': str(exc)}
        except Exception as exc:
            report['wire_evidence'] = {'status': 'FAIL', 'error': str(exc)}
        for family in spec['families']:
            try:
                print(f'Validating {family.upper()} bidirectional adjacency...', flush=True)
                peers = assess_family(family, FRR, csr_ip, frr_ll, csr_ll,
                                      spec['asn'], args.artifacts, spec['neighbor_timeout'])
                if peers['status'] != 'PASS':
                    report[family] = {'status': 'FAIL', 'error': 'Adjacency missing', 'peers': peers}
                else:
                    print(f'Validating {family.upper()} internal routes in both RIBs...', flush=True)
                    report[family] = assess_internal(family, csr_ip, args.artifacts,
                        routes['oe'][family], routes['csr'][family], spec['route_timeout'])
            except Exception as exc:
                report[family] = {'status': 'FAIL', 'error': str(exc)}
            print(f"{report[family]['status']} {family.upper()} internal route interoperability", flush=True)
        routes_pass = all(report[family]['status'] == 'PASS'
                          for family in spec['families'])
        wire_pass = (not spec.get('require_originated_updates')
                     or report.get('wire_evidence', {}).get('status') == 'PASS')
        passive_pass = (not spec.get('require_ipv6_passive_runtime')
                        or report.get('ipv6_passive_runtime', {}).get('status') == 'PASS')
        rc = 0 if routes_pass and wire_pass and passive_pass else 1
    except Exception as exc:
        report['error'] = str(exc)
        print('FAIL setup/validation:', exc, file=sys.stderr, flush=True)
    finally:
        for name in (FRR, CSR):
            try:
                output = command(['docker', 'logs', name], timeout=20, check=False)
            except (OSError, subprocess.SubprocessError) as exc:
                output = str(exc)
            (args.artifacts / (name + '.log')).write_text(output, encoding='utf8')
        (args.artifacts / 'report.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf8')
        if rc:
            for family in spec['families']:
                if report[family]['status'] == 'NOT_RUN':
                    print(f'NOT_RUN {family.upper()} (setup failed)', flush=True)
    return rc


if __name__ == '__main__':
    sys.exit(main())
