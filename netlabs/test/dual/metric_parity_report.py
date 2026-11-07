#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Evaluate exact Cisco wide-metric parity from a 2R-routes live report.

This report checker uses Cisco's observed default-K wide metric calculation.
It does not replace the live route driver or validate arbitrary K sets.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'common'))
from metric import (cisco_wide_metric, require_tlv2_types,
                    redist_seed_matches, TLV2_EXTERNAL)


def audit_report(report):
    """Return a list of failures; do not accept arithmetic mismatches as PASS."""
    failures = []
    for af in ('ipv4', 'ipv6'):
        internal = report['objectives'][f'DUAL-01/{af}']
        topology = report['objectives'][f'DUAL-04/{af}']
        if internal.get('status') != 'PASS' or topology.get('status') != 'PASS':
            failures.append(f'{af}: prerequisite DUAL-01/04 not PASS')
            continue
        for sender, receiver in (('oe', 'ce'), ('ce', 'oe')):
            route = internal['wire'][sender]
            try:
                require_tlv2_types(route, 'internal')
                expected_set = {cisco_wide_metric(v) for v in route['metrics']}
            except ValueError as exc:
                failures.append(f'{af} {sender}: {exc}')
                continue
            actual = topology['single_path_distances'][receiver]['rd']
            if len(expected_set) != 1 or actual not in expected_set:
                failures.append(f'{af} {sender}->{receiver}: reported distance '
                                f'{actual} != Cisco wide metric '
                                f'{sorted(expected_set)}')
        # Both origins use the same default-metric 100000 10 255 1 1500.
        external = report['objectives'][f'DUAL-02/{af}']
        if external.get('status') != 'PASS':
            failures.append(f'{af}: prerequisite DUAL-02 not PASS')
        else:
            for sender in ('oe', 'ce'):
                route = external['wire'][sender]
                try:
                    require_tlv2_types(route, 'external')
                except ValueError as exc:
                    failures.append(f'{af} {sender}: {exc}')
                    continue
                if not all(redist_seed_matches(v) for v in route['metrics']):
                    failures.append(f'{af} {sender}: external seed TLV2 units '
                                    'do not match 100000 kbit/s, 10 delay units, MTU 1500')
        lifecycle = report['objectives'][f'DUAL-03/{af}']
        if lifecycle.get('status') != 'PASS':
            failures.append(f'{af}: prerequisite DUAL-03 not PASS')
            continue
        for sender, receiver in (('oe', 'ce'), ('ce', 'oe')):
            change = lifecycle[sender]
            before = change['before_distance']
            after = change['after_distance']
            values = change['changed_metrics']
            if change.get('changed_types') != [TLV2_EXTERNAL] or not values:
                failures.append(f'{af} {sender}: changed TLV2 advertisement missing')
                continue
            try:
                expected = {cisco_wide_metric(v) for v in values}
            except ValueError as exc:
                failures.append(f'{af} {sender}: {exc}')
                continue
            if after['rd'] not in expected:
                failures.append(f'{af} {sender}->{receiver}: changed RD '
                                f'{after["rd"]} != wire metric {sorted(expected)}')
            if after['rd'] <= before['rd'] or after['cd'] <= before['cd']:
                failures.append(f'{af} {sender}->{receiver}: increasing seed '
                                f'delay caused non-increasing RD/CD '
                                f"({before['rd']}/{before['cd']} -> "
                                f"{after['rd']}/{after['cd']})")
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path, help='2R-routes report.json')
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding='utf-8'))
    if report.get('scenario') != '2R-routes':
        parser.error('expected a 2R-routes report')
    failures = audit_report(report)
    for failure in failures:
        print('FAIL:', failure)
    if failures:
        return 1
    print('PASS: exact Cisco default-K wide metric parity')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
