#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Qualify live AddPath advertisements against a Cisco CSR recipient."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
import capture
from interop import Cisco, CapturePrerequisiteError, Lab, assert_cli, command
from interop import report_base, require_packet_access
from interop import result, rib_prefixes, write_report
import wire

AS = 4473
SPOKES = ('r2', 'r3')
FAMILIES = ('ipv4', 'ipv6')


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def attributes(tlv, family):
    """Parse TLV2 extended-attribute words (Cisco type 0x07)."""
    raw = bytes.fromhex(tlv.get('extended_attributes_hex', ''))
    size = 4 if family == 'ipv4' else 16
    external = tlv.get('route_kind') == 'external'
    expected = size + 8 + (2 if external else 0)
    paths = []
    offset = 0
    while offset < len(raw):
        require(offset + 2 <= len(raw), 'incomplete AddPath attribute header')
        kind, words = raw[offset:offset + 2]
        length = words * 2
        offset += 2
        require(length and offset + length <= len(raw), 'malformed AddPath attribute length')
        if kind == 7:
            require(length == expected, 'incorrect Cisco AddPath payload length')
            nexthop = ipaddress.ip_address(raw[offset:offset + size])
            require(not nexthop.is_unspecified and not nexthop.is_multicast,
                    'invalid AddPath next hop')
            paths.append(str(nexthop))
        offset += length
    require(len(paths) == len(set(paths)), 'duplicate AddPath next hops')
    return paths


def route_updates(events, family, prefix, sender):
    """Only reliable UPDATEs advertising this exact destination from the hub."""
    for event in events:
        if (event.get('family') != family or event.get('source') != sender or
                event.get('opcode_name') != 'UPDATE' or not event.get('sequence')):
            continue
        for tlv in event.get('tlvs', []):
            if (tlv.get('codec') == 'TLV2' and
                    tlv.get('route_kind') == 'external' and
                    prefix in tlv.get('prefixes', [])):
                yield event, tlv


def require_update(events, family, prefix, sender, extra):
    matches = [(packet, tlv, attributes(tlv, family))
               for packet, tlv in route_updates(events, family, prefix, sender)]
    matches = [(packet, tlv, paths) for packet, tlv, paths in matches
               if (len(paths) >= 1 if extra else len(paths) == 0) and
               tlv['metric']['delay'] != 0xffffffffffff]
    require(matches, f'{family} replacement UPDATE missing (AddPath {extra})')
    packet, tlv, paths = matches[-1]
    if extra:
        base = ipaddress.ip_address(tlv['next_hop'])
        require(str(base) not in paths and not base.is_unspecified,
                'AddPath base next hop not distinct from additional path')
    return {'sequence': packet['sequence'], 'next_hop': tlv['next_hop'],
            'addpaths': paths, 'metric': tlv['metric']}


class InteropLab:
    def __init__(self, topology, artifacts):
        self.topology = topology.resolve()
        self.artifacts = artifacts.resolve()
        self.info = yaml.safe_load(topology.read_text())
        self.name = self.info['name']
        self.nodes = {role: f'clab-{self.name}-{role}'
                      for role in ('hub', 'r2', 'r3', 'csr')}
        self.csr_ip = None

    def save(self, name, text):
        (self.artifacts / name).write_text(str(text), encoding='utf8')

    def router(self, role, cmd):
        return assert_cli(command(['docker', 'exec', self.nodes[role],
                                   'vtysh', '-c', cmd], timeout=30), cmd)

    def configure(self, role, lines):
        args = ['docker', 'exec', self.nodes[role], 'vtysh',
                '-c', 'configure terminal']
        for line in lines:
            args += ['-c', line]
        return assert_cli(command(args, timeout=45), f'{role} config')

    def cisco(self, cmd):
        with Cisco(self.csr_ip, self.artifacts / 'cisco-cli.log') as router:
            return router.run(cmd)

    def deploy(self):
        require(self.info.get('provider') == 'clab' and
                set(self.info.get('nodes', {})) == set(self.nodes) and
                re.fullmatch(r'eigrp-[A-Za-z0-9_-]{1,10}', self.name),
                'unsupported 4R netlab topology')
        image = self.info['defaults']['devices.frr.clab.image']
        for role, tag in [('oe', image),
                          ('ce', self.info['defaults']['devices.csr.clab.image'])]:
            info = json.loads(command(['docker', 'image', 'inspect', tag], timeout=20))[0]
            self.save(f'image-{role}.json', json.dumps({
                'image': tag, 'id': info.get('Id'),
                'repo_digests': info.get('RepoDigests')}, indent=2))
        self.save('deploy.log', command(['netlab', 'up'],
                                        cwd=self.topology.parent, timeout=900))
        for role in ('hub', 'r2', 'r3'):
            Lab.wait(lambda role=role: self.router(role, 'show version'),
                     lambda text: bool(text.strip()), 150)
        info = json.loads(command(['docker', 'inspect', self.nodes['csr']]))[0]
        for network in info['NetworkSettings']['Networks'].values():
            if network.get('IPAddress'):
                self.csr_ip = network['IPAddress']
                break
        require(self.csr_ip, 'Cisco CSR management IP unavailable')
        Lab.wait(lambda: self.cisco('show running-config'),
                 lambda output: 'router eigrp OpenEIGRP' in output and
                 'address-family ipv6 unicast autonomous-system 4473' in output,
                 450)
        self.save('cisco-version.log', self.cisco('show version'))
        self.save('hub-version.log', self.router('hub', 'show version'))
        # The interface assignment is part of the fixture, not an assumption
        # about arbitrary netlab link ordering.
        assigned = command(['docker', 'exec', self.nodes['hub'],
                            'ip', '-4', 'addr', 'show', 'dev', 'eth3'])
        require('10.44.55.1/30' in assigned,
                'hub Cisco-facing interface is not eth3')
        return image

    def hub_address(self, family):
        if family == 'ipv4':
            return '10.44.55.1'
        output = command(['docker', 'exec', self.nodes['hub'],
                          'ip', '-6', 'addr', 'show', 'dev', 'eth3',
                          'scope', 'link'])
        for token in output.split():
            try:
                value = ipaddress.ip_interface(token).ip
            except ValueError:
                continue
            if value.is_link_local:
                return str(value)
        raise AssertionError('hub IPv6 link-local source unavailable')

    def external_route(self, family, prefix):
        command_line = f'show {"ip" if family == "ipv4" else "ipv6"} route eigrp'
        output = self.cisco(command_line)
        return ipaddress.ip_network(prefix) in rib_prefixes(
            output, 'ce', family, 'external')

    def route_wait(self, family, prefix, present=True, seconds=120):
        Lab.wait(lambda: self.external_route(family, prefix),
                 lambda found: found == present, seconds)

    def spokes_wait(self, family, prefix):
        """Wait for both learned paths before forcing the next initial sync."""
        def ready(output):
            start = output.find(prefix)
            if start < 0:
                return False
            # A new topology destination header ends the current block.
            block = re.split(r'\n\s*[PA]\s+\d', output[start:])[0]
            return len(re.findall(r'\bvia\b', block, re.I)) >= 2

        Lab.wait(lambda: self.router(
            'hub', f'show eigrp address-family {family} {AS} topology all-links'),
            ready, 160)

    def origin_set(self, role, family, prefix, add=True):
        verb = '' if add else 'no '
        command_line = f'{verb}{"ip" if family == "ipv4" else "ipv6"} route {prefix} Null0'
        self.configure(role, [command_line])

    def addpath_set(self, family, enabled):
        af = f'address-family {family} unicast autonomous-system {AS}'
        self.configure('hub', [
            'router eigrp OpenEIGRP', af, 'af-interface eth3',
            'no next-hop-self' if enabled else 'next-hop-self',
            'add-paths 2' if enabled else 'no add-paths'])
        if enabled:
            config = self.router('hub', 'show running-config')
            require('add-paths 2' in config and 'no next-hop-self' in config,
                    'hub did not retain requested AddPath settings')

    def link(self, up):
        command(['docker', 'exec', self.nodes['hub'], 'ip', 'link',
                 'set', 'eth3', 'up' if up else 'down'])

    def evidence(self, phase, family, action):
        """Capture before stimulus; retain original errors during cleanup."""
        name = f'{phase}-{family}'
        pcap = self.artifacts / f'{name}.pcap'
        state = self.artifacts / f'{name}.capture.json'
        capture.start('eth1', pcap, state, container=self.nodes['csr'])
        error = None
        try:
            action(pcap)
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                capture.stop(state, require_packets=error is None)
                events, errors = wire.read_pcap(pcap)
                self.save(name + '.json', json.dumps({
                    'events': events, 'errors': errors}, indent=2))
                if error is None:
                    require(not errors, 'packet decoding failed: ' + str(errors))
            except Exception as secondary:
                if error is None:
                    raise
                self.save(name + '.capture-error.log', repr(secondary))
        return events

    def wait_update(self, pcap, family, prefix, sender, extra, need_eot=False,
                    seconds=90):
        deadline = time.monotonic() + seconds
        last = 'no matching UPDATE captured'
        while time.monotonic() < deadline:
            try:
                events, errors = wire.read_pcap(pcap)
                if not errors:
                    record = require_update(events, family, prefix, sender, extra)
                    if not need_eot or any(
                            event.get('family') == family and
                            event.get('source') == sender and
                            event.get('opcode_name') == 'UPDATE' and
                            'EOT' in event.get('flag_names', [])
                            for event in events):
                        return record
                    last = 'route UPDATE found, EOT not yet captured'
                else:
                    last = str(errors)
            except (AssertionError, OSError, ValueError) as exc:
                last = str(exc)
            time.sleep(0.25)
        raise AssertionError(f'{family} UPDATE capture timed out: {last}')

    def neighbor_wait(self, family, present, seconds=90):
        def check(output):
            found = re.search(r'\beth3\b', output, re.I) is not None
            return found == present
        Lab.wait(lambda: self.router(
            'hub', f'show eigrp address-family {family} {AS} neighbors'),
            check, seconds)

    def snapshot(self, label):
        for role in ('hub', 'r2', 'r3'):
            for family in FAMILIES:
                try:
                    self.save(f'{label}-{role}-{family}.log', self.router(
                        role, f'show eigrp address-family {family} {AS} topology all-links'))
                except Exception as exc:
                    self.save(f'{label}-{role}-{family}.error', str(exc))
        if self.csr_ip:
            for family in FAMILIES:
                try:
                    self.save(f'{label}-csr-{family}.log', self.cisco(
                        f'show {"ip" if family == "ipv4" else "ipv6"} route eigrp'))
                except Exception as exc:
                    self.save(f'{label}-csr-{family}.error', str(exc))

    def collect_logs(self):
        for role, node in self.nodes.items():
            self.save(f'{role}-container.log', command(
                ['docker', 'logs', node], timeout=20, check=False))


def case(report, name, action):
    try:
        report['objectives'][name] = result('PASS', **action())
    except Exception as exc:
        report['objectives'][name] = result('FAIL', error=str(exc))
    print(report['objectives'][name]['status'], name, flush=True)
    return report['objectives'][name]['status'] == 'PASS'


def run_family(lab, report, family, prefix, repeats):
    sender = lab.hub_address(family)
    for role in SPOKES:
        lab.origin_set(role, family, prefix)
    lab.addpath_set(family, True)
    lab.spokes_wait(family, prefix)
    lab.snapshot('pre-' + family)

    def init():
        records = []
        for iteration in range(repeats):
            lab.link(False)
            try:
                lab.neighbor_wait(family, False)
                # Begin capture before restoring the adjacency.
                events = lab.evidence(
                    f'initial-{iteration + 1}', family,
                    lambda pcap: startup_link(lab, family, prefix, pcap, sender))
            finally:
                lab.link(True)
            record = require_update(events, family, prefix, sender, True)
            require(any(e.get('family') == family and
                        e.get('source') == sender and
                        e.get('opcode_name') == 'UPDATE' and
                        'EOT' in e.get('flag_names', []) for e in events),
                    f'initial sync {iteration + 1} did not include an EOT')
            records.append(record)
        return {'syncs': records, 'metric': records[-1]['metric']}

    initial = {}
    def check_init():
        initial.update(init())
        return {'syncs': initial['syncs']}

    if not case(report, f'AP-INIT/{family}', check_init):
        for suffix in ('WITHDRAW', 'RECOVER'):
            report['objectives'][f'AP-{suffix}/{family}'] = result(
                'BLOCKED', reason='initial sync never established AddPath evidence')
        return

    def check_withdraw():
        events = lab.evidence('partial-withdraw', family, lambda pcap: (
            lab.origin_set('r3', family, prefix, False),
            lab.wait_update(pcap, family, prefix, sender, False)))
        record = require_update(events, family, prefix, sender, False)
        expected = initial['metric']
        for key in ('delay', 'bandwidth'):
            require(record['metric'][key] == expected[key],
                    f'partial withdrawal changed destination {key}')
        require(lab.external_route(family, prefix),
                'Cisco removed the surviving destination')
        return {'replacement': record}

    if not case(report, f'AP-WITHDRAW/{family}', check_withdraw):
        report['objectives'][f'AP-RECOVER/{family}'] = result(
            'BLOCKED', reason='partial withdrawal failed')
        return

    def check_recover():
        events = lab.evidence('recover', family, lambda pcap: (
            lab.origin_set('r3', family, prefix, True),
            lab.wait_update(pcap, family, prefix, sender, True)))
        record = require_update(events, family, prefix, sender, True)
        require(lab.external_route(family, prefix), 'Cisco lost route on recovery')
        return {'readvertisement': record}

    case(report, f'AP-RECOVER/{family}', check_recover)


def startup_link(lab, family, prefix, pcap, sender):
    lab.link(True)
    lab.neighbor_wait(family, True, 160)
    lab.route_wait(family, prefix, True, 160)
    lab.wait_update(pcap, family, prefix, sender, True,
                    need_eot=True, seconds=160)


def validate(spec, name):
    require(isinstance(spec, dict) and
            spec.get('schema') == 'openeigrp-live-addpath-compat/v1' and
            spec.get('name') == name and spec.get('group') == 'dual' and
            spec.get('families') == list(FAMILIES) and
            spec.get('origins') == list(SPOKES) and
            spec.get('hub') == 'hub' and spec.get('receiver') == 'csr' and
            spec.get('add_paths') == 2 and
            isinstance(spec.get('ipv6_sync_repeats'), int) and
            2 <= spec['ipv6_sync_repeats'] <= 8 and
            isinstance(spec.get('prefixes'), dict),
            'invalid 4R AddPath scenario fixture')
    for family in FAMILIES:
        prefix = spec['prefixes'].get(family)
        require(isinstance(prefix, str) and
                ipaddress.ip_network(prefix).version == (4 if family == 'ipv4' else 6),
                f'invalid {family} destination prefix')


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    name = Path(__file__).resolve().parent.name
    spec = yaml.safe_load(args.scenario.read_text())
    validate(spec, name)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    lab = InteropLab(args.topology, args.artifacts)
    report = report_base(name, lab)
    report['mix'] = {'oe': 3, 'ce': 1}
    report['required'] = [f'AP-{case}/{family}' for family in FAMILIES
                          for case in ('INIT', 'WITHDRAW', 'RECOVER')]
    report['objectives'] = {}
    deployed = False
    try:
        require_packet_access()
        report['oe_image'] = lab.deploy()
        deployed = True
        report['ce_image'] = lab.info['defaults']['devices.csr.clab.image']
        for family in FAMILIES:
            try:
                run_family(lab, report, family, spec['prefixes'][family],
                           spec['ipv6_sync_repeats'] if family == 'ipv6' else 1)
            finally:
                lab.snapshot('end-' + family)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        for key in report['required']:
            report['objectives'].setdefault(key, result('BLOCKED', reason=str(exc)))
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        # The runner owns topology teardown and packet marker recovery.
        # Explicitly restore only settings changed by this scenario.
        if deployed:
            for family in FAMILIES:
                for role in SPOKES:
                    try:
                        lab.origin_set(role, family, spec['prefixes'][family], False)
                    except Exception as exc:
                        report.setdefault('rollback_errors', []).append(str(exc))
                try:
                    lab.addpath_set(family, False)
                except Exception as exc:
                    report.setdefault('rollback_errors', []).append(str(exc))
            try:
                lab.link(True)
                lab.snapshot('final')
                lab.collect_logs()
            except Exception as exc:
                report.setdefault('rollback_errors', []).append(str(exc))
        for key in report['required']:
            report['objectives'].setdefault(key, result('NOT_RUN'))
        states = [report['objectives'][key]['status'] for key in report['required']]
        report['status'] = ('PASS' if all(s == 'PASS' for s in states) and
                            not report.get('rollback_errors') else
                            'BLOCKED' if all(s == 'BLOCKED' for s in states) else
                            'FAIL')
        write_report(args.artifacts, report)
    print(report['status'], name, flush=True)
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
