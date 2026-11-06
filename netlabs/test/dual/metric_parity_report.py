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


WIDE_SCALE = 65536
BANDWIDTH_CONSTANT = 10000000
PICOSECONDS_PER_MICROSECOND = 1000000
SEED_BANDWIDTH = 100000
SEED_DELAY_PS = 10 * 10000000


def cisco_wide_metric(metric):
    """Cisco default-K distance from TLV2 bandwidth (kbit/s) and delay (ps)."""
    bandwidth = metric['bandwidth']
    delay = metric['delay']
    if not isinstance(bandwidth, int) or bandwidth <= 0:
        raise ValueError('invalid TLV2 bandwidth')
    if not isinstance(delay, int) or delay < 0:
        raise ValueError('invalid TLV2 delay')
    return ((BANDWIDTH_CONSTANT * WIDE_SCALE) // bandwidth
            + (delay * WIDE_SCALE) // PICOSECONDS_PER_MICROSECOND)


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
            metrics = internal['wire'][sender]['metrics']
            expected_set = {cisco_wide_metric(v) for v in metrics}
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
                vectors = external['wire'][sender]['metrics']
                if not vectors or any(v['bandwidth'] != SEED_BANDWIDTH
                                      or v['delay'] != SEED_DELAY_PS
                                      for v in vectors):
                    failures.append(f'{af} {sender}: external seed TLV2 units '
                                    'do not match 100000 kbit/s, 10 delay units')
        lifecycle = report['objectives'][f'DUAL-03/{af}']
        if lifecycle.get('status') != 'PASS':
            failures.append(f'{af}: prerequisite DUAL-03 not PASS')
            continue
        for sender, receiver in (('oe', 'ce'), ('ce', 'oe')):
            change = lifecycle[sender]
            before = change['before_distance']
            after = change['after_distance']
            values = change['changed_metrics']
            if not values:
                failures.append(f'{af} {sender}: changed advertisement missing')
                continue
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
