#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Live Cisco classic MD5 <-> OpenEIGRP named MD5 interoperability.

Neighbor and RIB checks are independent of strict protocol-88 wire evidence.
The MD5 digest hypotheses from OE-03 remain diagnostic, not PASS criteria.
The remote runner owns containerlab teardown.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import sys
import time

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'common'))
from interop import (Lab, CapturePrerequisiteError, capture, command,
                     require_packet_access, result, write_report)
import wire

CASES = ('MDI-01', 'MDI-02', 'MDI-03', 'MDI-04', 'MDI-05', 'MDI-06', 'MDI-07')
ROLES = ('oe', 'ce')
MULTICAST = '224.0.0.10'
UNREACHABLE = (0xffffffff, 0xffffffffffff)
ORIGIN_IF = 'md5rt0'
ORIGIN_PEER = 'md5rt1'


def require(value, message):
    if not value:
        raise AssertionError(message)


def validate(spec, topology):
    require(spec.get('schema') == 'openeigrp-live-md5-interop/v1', 'invalid schema')
    require(spec.get('name') == '2R-md5-interop', 'invalid scenario name')
    require(spec.get('family') == 'ipv4' and spec.get('asn') == 4473,
            'unsupported MD5 test family/AS')
    require(tuple(spec.get('objectives', [])) == CASES, 'objective mismatch')
    require(tuple(spec.get('nodes', {})) == ROLES, 'unexpected router roles')
    require(spec.get('keychain') == 'OE03C2OE' and spec.get('key_id') == 7 and
            spec.get('key_string') == 'oe03-lab-md5', 'unexpected MD5 fixture')
    require(spec.get('origin') == {'kind': 'veth', 'interface': ORIGIN_IF,
                                   'peer': ORIGIN_PEER},
            'dedicated veth route stimulus required')
    require(spec.get('diagnostics') == {'cisco_packet_debug': True},
            'Cisco packet rejection diagnostics required')
    require(topology.get('name') == 'eigrp-2r-md5' and
            topology.get('provider') == 'clab', 'unexpected netlab topology')
    nodes = topology.get('nodes', {})
    require(set(nodes) == {'oe', 'csr'} and nodes['oe'].get('device') == 'frr' and
            nodes['csr'].get('device') == 'csr', 'OE and CSR nodes required')
    require(all(not nodes[n].get('config') for n in nodes),
            'EIGRP must be enabled after packet capture begins')
    require(not topology.get('module'), 'netlab EIGRP auto-configuration forbidden')
    require(re.fullmatch(r'eigrp-[a-z0-9-]{1,10}', topology['name']) is not None,
            'netlab topology name exceeds preflight limit')
    for role in ROLES:
        item = spec['nodes'][role]
        require(ipaddress.ip_network(item['advertised'], strict=True).prefixlen == 32,
                'only host route stimuli supported')
        require(item['neighbor'] == spec['nodes'][ROLES[1 - ROLES.index(role)]]['address'],
                'neighbor address mismatch')
    link = topology['links'][0]
    require(len(topology['links']) == 1 and
            link['oe']['ipv4'] == spec['nodes']['oe']['address'] + '/30' and
            link['csr']['ipv4'] == spec['nodes']['ce']['address'] + '/30',
            'scenario and link addressing differ')


class Md5Lab(Lab):
    def _wait_ce(self, seconds):
        # Unlike the shared named-mode 2R lab, we configure Cisco classic
        # EIGRP only after capture starts. Netlab still owns initial L3 setup.
        address = re.escape(self.spec['nodes']['ce']['address'])
        return self.wait(lambda: self.ce_cli('show ip interface brief'),
                         lambda output: any(re.search(
                             r'^GigabitEthernet2\s+' + address + r'\s+.*\bup\s+up\s*$',
                             line.strip()) for line in output.splitlines()), seconds)

    def __init__(self, topology, artifacts, spec):
        super().__init__(topology, artifacts)
        self.spec = spec
        self.origin_created = False

    def configure_md5(self):
        chain = self.spec['keychain']
        key_id = self.spec['key_id']
        key = self.spec['key_string']
        # Keychains and interface MD5 mode are established before either
        # EIGRP process is enabled. Cisco uses proven classic IOS syntax.
        self.oe_config([f'key chain {chain}', f'key {key_id}', f'key-string {key}'])
        self.ce_config([f'key chain {chain}', f'key {key_id}',
                        f'key-string {key}', 'exit', 'exit',
                        'interface GigabitEthernet2',
                        f'ip authentication key-chain eigrp 4473 {chain}',
                        'ip authentication mode eigrp 4473 md5'])
        rid = self.spec['nodes']['oe']['router_id']
        self.oe_config(['router eigrp OpenEIGRP',
                        'address-family ipv4 unicast autonomous-system 4473',
                        f'eigrp router-id {rid}',
                        f'network {self.spec["nodes"]["oe"]["address"]} 0.0.0.0',
                        f'network {rid} 0.0.0.0',
                        'af-interface eth1', 'authentication key-chain ' + chain,
                        'authentication mode md5', 'no passive-interface',
                        'no shutdown', 'exit-af-interface', 'no shutdown'])
        rid = self.spec['nodes']['ce']['router_id']
        self.ce_config(['router eigrp 4473', f'eigrp router-id {rid}',
                        'no auto-summary', 'network 10.44.53.0 0.0.0.3',
                        f'network {rid} 0.0.0.0'])
        oe = self.oe_cli('show running-config')
        ce = self.ce_cli('show running-config')
        self.save('oe-md5-config.log', oe)
        self.save('cisco-md5-config.log', ce)
        require('authentication mode md5' in oe and
                'authentication key-chain ' + chain in oe and
                'router eigrp OpenEIGRP' in oe and 'key chain ' + chain in oe,
                'OpenEIGRP MD5 configuration not retained')
        require('ip authentication mode eigrp 4473 md5' in ce and
                f'ip authentication key-chain eigrp 4473 {chain}' in ce and
                'router eigrp 4473' in ce and 'key chain ' + chain in ce,
                'Cisco MD5 configuration not retained')
        if self.spec['diagnostics']['cisco_packet_debug']:
            # IOS packet-debug diagnoses rejected MD5 packets. Failure to
            # enable optional diagnostics must not change the UUT verdict.
            try:
                output = self.ce_cli('debug eigrp packets')
            except Exception as exc:
                output = f'Cisco packet debugging unavailable: {exc}\n'
            self.save('ce-debug-enable.log', output)

    def origin_prepare(self):
        """Give the stimulus its own primary IPv4 interface, not a second lo address.

        Zebra/FRR discovers the new interface through its normal netlink path;
        the subsequent EIGRP network command admits its connected /32.
        The peer must also be up for veth carrier to be operational.
        """
        if self.origin_created:
            return
        for args in (('link', 'add', ORIGIN_IF, 'type', 'veth',
                      'peer', 'name', ORIGIN_PEER),
                     ('link', 'set', ORIGIN_PEER, 'up'),
                     ('link', 'set', ORIGIN_IF, 'up'),
                     ('address', 'add', self.spec['nodes']['oe']['advertised'],
                      'dev', ORIGIN_IF)):
            command(['docker', 'exec', self.oe, 'ip', *args])
        self.origin_created = True

    def origin_state(self, *, present, phase):
        """Require actual local connected-route admission before remote RIB checks."""
        prefix = self.spec['nodes']['oe']['advertised']
        def read():
            # The AS argument follows 'topology' in the named-mode VTY
            # grammar; putting it before 'topology' rejects the command.
            cmd = f'show eigrp address-family ipv4 topology 4473 {prefix}'
            try:
                output = self.oe_cli(cmd)
            except RuntimeError as exc:
                if '% Unknown command' in str(exc):
                    raise AssertionError(f'unsupported topology CLI: {cmd}') from exc
                raise
            self.save(f'oe-origin-{phase}.log', output)
            return output
        self.wait(read,
                  lambda text: ('via Connected, ' + ORIGIN_IF in text) == present,
                  40)

    def change_route(self, role, *, add):
        address = self.spec['nodes'][role]['advertised'].split('/')[0]
        if role == 'oe':
            if add:
                self.origin_prepare()
            # Keep the veth and its connected kernel address intact through
            # withdrawal, so that this phase tests EIGRP advertisement
            # retirement rather than interface teardown or carrier loss.
            lines = ['router eigrp OpenEIGRP',
                     'address-family ipv4 unicast autonomous-system 4473']
            if add:
                lines += ['af-interface ' + ORIGIN_IF, 'passive-interface',
                          'exit-af-interface']
            lines.append(('network ' if add else 'no network ') +
                         address + ' 0.0.0.0')
            self.oe_config(lines)
        elif role == 'ce':
            if add:
                self.ce_config(['interface Loopback100',
                                f'ip address {address} 255.255.255.255',
                                'no shutdown', 'exit', 'router eigrp 4473',
                                f'network {address} 0.0.0.0'])
            else:
                self.ce_config(['router eigrp 4473',
                                f'no network {address} 0.0.0.0',
                                'exit', 'no interface Loopback100'])
        else:
            raise ValueError('unknown route source')

    def route_state(self, *, present):
        prefixes = {'oe': [self.spec['nodes']['ce']['advertised']],
                    'ce': [self.spec['nodes']['oe']['advertised']]}
        return self.check_routes('ipv4', prefixes, 'internal',
                                 present=present, seconds=150)

    def stable_peers(self):
        self.peers('ipv4', True, 150)
        time.sleep(7)
        self.peers('ipv4', True, 150)

    def snapshot_md5(self):
        for role in ROLES:
            for tag, reader in (('neighbors', lambda r=role: self.neighbor(r, 'ipv4')),
                                ('rib', lambda r=role: self.routes(r, 'ipv4')),
                                ('running', lambda r=role: self.oe_cli('show running-config')
                                 if r == 'oe' else self.ce_cli('show running-config'))):
                try:
                    self.save(f'{role}-{tag}.log', reader())
                except Exception as exc:
                    self.save(f'{role}-{tag}.log', str(exc))
        for role, container in (('oe', self.oe), ('ce', self.ce)):
            self.save(f'{role}-container.log', command(
                ['docker', 'logs', container], timeout=20, check=False))
        # watchfrr/docker logs do not contain FRR's daemon file output. Save
        # both peers' packet rejection diagnostics before runner cleanup.
        diagnostics = (
            ('oe-daemon.log', lambda: command(
                ['docker', 'exec', self.oe, 'cat', '/var/log/frr/frr.log'],
                timeout=25, check=False)),
            ('ce-logging.log', lambda: self.ce_cli('show logging')),
            ('ce-eigrp-events.log', lambda: self.ce_cli('show ip eigrp events')),
        )
        for name, reader in diagnostics:
            try:
                self.save(name, reader())
            except Exception as exc:
                self.save(name, f'{name} unavailable: {exc}\n')


def auth_id(event):
    """Read the actual 40-byte Cisco MD5 Authentication TLV, when present."""
    tlvs = event['tlvs']
    if not tlvs or tlvs[0]['type'] != 2:
        return None  # Includes Cisco's legitimate header-only RTP ACKs.
    tlv = tlvs[0]
    body = bytes.fromhex(tlv['value_hex'])
    require(tlv['length'] == 40 and len(body) == 36,
            'malformed MD5 Authentication TLV length')
    require(int.from_bytes(body[:2], 'big') == 2 and
            int.from_bytes(body[2:4], 'big') == 16,
            'unexpected EIGRP Authentication TLV type or digest length')
    return int.from_bytes(body[4:8], 'big')


def authenticated(events, source, key_id, *, begin=0, end=float('inf'), opcode=None):
    return [e for e in events if e['source'] == source and
            begin <= e['timestamp'] < end and
            (opcode is None or e['opcode_name'] == opcode) and
            auth_id(e) == key_id]


def acknowledged(events, packet, peer, *, end=float('inf')):
    """A wire ACK must originate from the remote peer after its UPDATE."""
    return packet['sequence'] != 0 and any(
        e['source'] == peer and e['destination'] == packet['source'] and
        packet['timestamp'] <= e['timestamp'] < end and
        e['ack'] == packet['sequence'] for e in events)


def routes(events, sender, key_id, prefix, *, begin, end, withdrawn):
    accepted = []
    for e in authenticated(events, sender, key_id, begin=begin, end=end):
        if e['opcode_name'] not in ('UPDATE', 'QUERY') or not e['sequence']:
            continue
        for tlv in e['tlvs']:
            if prefix not in tlv.get('prefixes', []) or tlv.get('route_kind') != 'internal':
                continue
            unreachable = tlv.get('metric', {}).get('delay') in UNREACHABLE
            if unreachable == withdrawn:
                accepted.append(e)
                break
    return accepted


def verify_wire(events, spec, stages, *, through='recover'):
    """Gate one completed phase and all its prerequisite wire evidence.

    Earlier phases can pass even when a later route operation fails. Packet
    timestamps are compared to the explicit stimulus boundaries, not inferred
    from potentially stale RIB/neighbor output.
    """
    phases = ('hello', 'init', 'add', 'withdraw', 'recover')
    require(through in phases, 'unknown wire phase')
    requested = phases.index(through)
    require(events, 'empty protocol-88 capture')
    require({'auth', 'end'} <= stages.keys(), 'missing capture lifecycle boundary')
    require(stages['auth'] < stages['end'], 'invalid capture lifecycle window')
    for phase, following in (('add', 'withdraw'), ('withdraw', 'recover'),
                             ('recover', 'end')):
        if requested >= phases.index(phase):
            require({phase, following} <= stages.keys(),
                    f'missing successful {phase} lifecycle window')
            require(stages[phase] < stages[following],
                    f'invalid {phase} lifecycle window')
    hello_end = stages.get('add', stages['end'])
    require(stages['auth'] < hello_end, 'invalid authentication window')
    addresses = {role: spec['nodes'][role]['address'] for role in ROLES}
    sources = set(addresses.values())
    seen = [e for e in events if e['family'] == 'ipv4' and e['source'] in sources]
    require(seen, 'no traffic from either EIGRP peer')
    for e in seen:
        require(e['version'] == 2 and e['asn'] == spec['asn'],
                'unexpected EIGRP header version or AS')
    key = spec['key_id']
    summary = {}
    for role in ROLES:
        src = addresses[role]
        peer = addresses['ce' if role == 'oe' else 'oe']
        hello = [e for e in authenticated(seen, src, key,
                begin=stages['auth'], end=hello_end, opcode='HELLO')
                if e['destination'] == MULTICAST and e['sequence'] == 0]
        require(len(hello) >= 2, f'{role} missing two keyed multicast Hellos')
        counts = {}
        init_count = eot_count = 0
        if requested >= phases.index('init'):
            init = [e for e in authenticated(seen, src, key,
                    begin=stages['auth'], end=hello_end, opcode='UPDATE')
                    if e['flags'] & 1 and e['sequence']]
            require(init, f'{role} missing authenticated INIT UPDATE')
            require(any(acknowledged(seen, e, peer, end=hello_end) for e in init),
                    f'{role} missing INIT ACK')
            eot = [e for e in authenticated(seen, src, key,
                   begin=stages['auth'], end=hello_end, opcode='UPDATE')
                   if e['flags'] & 8 and e['sequence']]
            require(eot, f'{role} missing authenticated EOT UPDATE')
            init_count, eot_count = len(init), len(eot)
        prefix = spec['nodes'][role]['advertised']
        for phase, following, withdrawn in (('add', 'withdraw', False),
                                             ('withdraw', 'recover', True),
                                             ('recover', 'end', False)):
            if requested < phases.index(phase):
                break
            updates = routes(seen, src, key, prefix, begin=stages[phase],
                             end=stages[following], withdrawn=withdrawn)
            require(updates, f'{role} missing keyed {phase} route UPDATE/QUERY')
            require(any(acknowledged(seen, e, peer, end=stages[following])
                        for e in updates), f'{role} missing {phase} route ACK')
            counts[phase] = len(updates)
        summary[role] = {'hello': len(hello), 'init': init_count,
                         'eot': eot_count, 'route': counts}
    if requested >= phases.index('add'):
        # After MD5 is enabled, route-bearing UPDATE/QUERY packets are always
        # authenticated. Header-only Hello ACKs remain legal and unkeyed.
        for e in seen:
            if (e['timestamp'] < stages['add'] or
                e['timestamp'] >= stages[('withdraw', 'recover', 'end')[requested - 2]] or
                e['opcode_name'] not in ('UPDATE', 'QUERY')):
                continue
            if any(t.get('route_kind') for t in e['tlvs']):
                require(auth_id(e) == key, 'unauthenticated route-bearing EIGRP packet')
    return summary


def execute(lab, report, spec):
    events = []
    stages = {}
    pcap = lab.artifacts / 'cisco-openeigrp-md5.pcap'
    state = lab.artifacts / 'cisco-openeigrp-md5.capture.json'
    running = False
    try:
        lab.deploy()
        report['objectives'][CASES[0]] = result('PASS',
                                               image='OpenEIGRP and Cisco CSR1000v')
        capture.start('eth1', pcap, state, container=lab.oe)
        running = True
        stages['auth'] = time.time()
        lab.configure_md5()
        lab.stable_peers()
        report['objectives'][CASES[1]] = result('PASS', neighbors='both directions')
        stages['add'] = time.time()
        for role in ROLES:
            lab.change_route(role, add=True)
        lab.origin_state(present=True, phase='add')
        lab.route_state(present=True)
        lab.peers('ipv4', True, 150)
        time.sleep(6)
        report['rib_add'] = True
        stages['withdraw'] = time.time()
        for role in ROLES:
            lab.change_route(role, add=False)
        lab.origin_state(present=False, phase='withdraw')
        lab.route_state(present=False)
        lab.peers('ipv4', True, 150)
        time.sleep(6)
        report['rib_withdraw'] = True
        stages['recover'] = time.time()
        for role in ROLES:
            lab.change_route(role, add=True)
        lab.origin_state(present=True, phase='recover')
        lab.route_state(present=True)
        lab.stable_peers()
        time.sleep(6)
        report['rib_recover'] = True
        stages['end'] = time.time()
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        if running:
            if 'end' not in stages:
                stages['end'] = time.time()
            try:
                capture.stop(state, require_packets=True)
                events, diagnostics = wire.read_pcap(pcap, strict=True)
                require(not diagnostics, 'capture decoding errors')
                lab.save('md5-packets.json', json.dumps(events, indent=2) + '\n')
            except Exception as exc:
                report['capture_error'] = str(exc)
        report['stages'] = stages
        try:
            lab.snapshot_md5()
        except Exception as exc:
            report['snapshot_error'] = str(exc)

    if events:
        report['wire_errors'] = {}
        checks = (
            (CASES[2], 'hello', None, 'keyed_hellos'),
            (CASES[3], 'init', None, 'init_acked'),
            (CASES[4], 'add', 'rib_add', 'installed'),
            (CASES[5], 'withdraw', 'rib_withdraw', 'withdrawn'),
            (CASES[6], 'recover', 'rib_recover', 'recovered'),
        )
        for case, phase, rib, field in checks:
            if phase in ('withdraw', 'recover') and phase not in stages:
                report['objectives'][case] = result(
                    'BLOCKED', reason=f'{phase} not executed: preceding phase failed')
                continue
            try:
                summary = verify_wire(events, spec, stages, through=phase)
                if rib:
                    require(report.get(rib) is True,
                            f'{phase} RIB change was not verified')
                report['wire'] = summary
                report['objectives'][case] = result('PASS', **{field: 'both directions'})
            except Exception as exc:
                report['wire_errors'][case] = str(exc)
                report['objectives'][case] = result('FAIL', reason=str(exc))

    for case in CASES:
        report['objectives'].setdefault(case, result('FAIL', reason=(
            report.get('wire_error') or report.get('capture_error') or
            report.get('error') or 'missing required evidence')))
    report['status'] = ('PASS' if all(report['objectives'][case]['status'] == 'PASS'
                                      for case in CASES) else 'FAIL')
    return report['status'] == 'PASS'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--scenario', type=Path, required=True)
    parser.add_argument('--topology', type=Path, required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    args = parser.parse_args(argv)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    report = {'scenario': '2R-md5-interop', 'objectives': {}, 'status': 'NOT_RUN'}
    try:
        spec = yaml.safe_load(args.scenario.read_text())
        validate(spec, yaml.safe_load(args.topology.read_text()))
        require_packet_access()
        lab = Md5Lab(args.topology, args.artifacts, spec)
        passed = execute(lab, report, spec)
    except CapturePrerequisiteError as exc:
        report['error'] = str(exc)
        report['objectives'] = {case: result('BLOCKED', reason=str(exc)) for case in CASES}
        passed = False
    except Exception as exc:
        report['error'] = str(exc)
        report['objectives'] = {case: result('FAIL', reason=str(exc)) for case in CASES}
        passed = False
    report['required'] = CASES
    report['status'] = 'PASS' if passed else 'FAIL'
    write_report(args.artifacts, report)
    for case in CASES:
        print(report['objectives'][case]['status'], case, flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
