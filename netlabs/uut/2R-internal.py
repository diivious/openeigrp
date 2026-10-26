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

FRR = 'clab-eigrp-internal-oe'
CSR = 'clab-eigrp-internal-csr'
CISCO_IMAGE = 'vrnetlab/vr-csr:17.03.06'
# Cisco prints 'D   10.0.0.0/24'; FRR zebra prints 'D>* 10.0.0.0/24'.
# In contrast, external 'D EX' and connected 'C' entries cannot match.
INTERNAL = re.compile(r'^\s*D[>*+]*\s+([0-9a-fA-F:.]+/\d{1,3})(?=\s|$)', re.M)


def internal_route_present(output, prefix):
    """Require an exact EIGRP-internal RIB entry, not prefix/header text."""
    target = ipaddress.ip_network(prefix, strict=True)
    for candidate in INTERNAL.findall(output):
        try:
            if ipaddress.ip_network(candidate, strict=True) == target:
                return True
        except ValueError:
            continue
    return False


def route_tables(family, csr_ip, artifacts):
    verb = 'ip' if family == 'ipv4' else 'ipv6'
    cmd = f'show {verb} route eigrp'
    frr_output = command(['docker', 'exec', FRR, 'vtysh', '-c', cmd], timeout=20)
    assert_cli(frr_output, f'FRR {cmd}')
    with Cisco(csr_ip, artifacts / 'cisco-cli.log') as router:
        cisco_output = router.run(cmd)
    for target, output in [('frr', frr_output), ('csr', cisco_output)]:
        with (artifacts / f'{target}-{family}-routes.log').open('a', encoding='utf8') as log:
            log.write(f'\n$ {cmd}\n{output}\n')
    return frr_output, cisco_output


def assess_internal(family, csr_ip, artifacts, prefix_frr, prefix_csr, seconds):
    """Verify Cisco learned the OE prefix and OE learned Cisco's prefix."""
    deadline = time.monotonic() + seconds
    last_frr = last_cisco = ''
    while time.monotonic() < deadline:
        last_frr, last_cisco = route_tables(family, csr_ip, artifacts)
        received_by_frr = internal_route_present(last_frr, prefix_csr)
        received_by_csr = internal_route_present(last_cisco, prefix_frr)
        if received_by_frr and received_by_csr:
            return {'status': 'PASS', 'oe_learned': prefix_csr,
                    'cisco_learned': prefix_frr}
        time.sleep(min(6, max(0, deadline - time.monotonic())))
    return {'status': 'FAIL', 'error': 'EIGRP-internal route absent from one or both RIBs',
            'oe_expected': prefix_csr, 'cisco_expected': prefix_frr,
            'oe_rib': last_frr, 'cisco_rib': last_cisco}


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
        # The runner pins the newest installed release in the staged topology.
        image = topology['defaults']['devices.frr.clab.image']
        if not re.fullmatch(r'ghcr\.io/diivious/openeigrp-frr:[0-9]+\.[0-9]+\.[0-9]+', image):
            raise RuntimeError(f'Unexpected OpenEIGRP image reference: {image}')
        report['openeigrp_image'] = image
        for image in (image, CISCO_IMAGE):
            command(['docker', 'image', 'inspect', image], timeout=15)
        if not shutil.which('sshpass'):
            raise RuntimeError('sshpass is required for Cisco management access')
        print('Deploying and configuring OpenEIGRP and Cisco via netlab...', flush=True)
        # Unlike the detached CLI probes, netlab resolves topology-local paths.
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
        rc = 0 if all(report[family]['status'] == 'PASS' for family in spec['families']) else 1
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
