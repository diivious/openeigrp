#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression checks for Cisco wide-metric report validation."""

import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name('metric_parity_report.py')
spec = importlib.util.spec_from_file_location('metric_parity_report', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MetricParityReportTest(unittest.TestCase):
    def test_cisco_wide_metric_preserves_fractional_latency(self):
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 25600, 'delay': 2560}), 25600167)
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 8000000, 'delay': 1250000}), 163840)

    def test_cisco_wide_metric_uses_multiply_before_divide(self):
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 100000, 'delay': 100000000}), 13107200)
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 100000, 'delay': 1000000000}), 72089600)

    def test_invalid_bandwidth_rejected(self):
        with self.assertRaisesRegex(ValueError, 'bandwidth'):
            module.cisco_wide_metric({'bandwidth': 0, 'delay': 5})

    def test_regression_capture_is_rejected(self):
        import json
        fixture = Path(__file__).parent / 'fixtures' / 'metric-parity-20261105.json'
        report = json.loads(fixture.read_text(encoding='utf-8'))
        for af in ('ipv4', 'ipv6'):
            for route in report['objectives'][f'DUAL-01/{af}']['wire'].values():
                route['types'] = [0x0602]
            for route in report['objectives'][f'DUAL-02/{af}']['wire'].values():
                route['types'] = [0x0603]
            for role in ('oe', 'ce'):
                report['objectives'][f'DUAL-03/{af}'][role]['changed_types'] = [0x0603]
        failures = module.audit_report(report)
        self.assertTrue(any('ce->oe' in f and 'reported distance' in f
                            for f in failures), failures)
        self.assertTrue(any('external seed TLV2 units' in f
                            for f in failures), failures)
        self.assertTrue(any('non-increasing RD/CD' in f
                            for f in failures), failures)

    def test_rejects_missing_or_mixed_codec_evidence(self):
        import json
        fixture = Path(__file__).parent / 'fixtures' / 'metric-parity-20261105.json'
        report = json.loads(fixture.read_text(encoding='utf-8'))
        failures = module.audit_report(report)
        self.assertTrue(any('TLV2 codec evidence' in f for f in failures), failures)

    def test_corrected_reference_report_passes(self):
        import copy
        import json
        fixture = Path(__file__).parent / 'fixtures' / 'metric-parity-20261105.json'
        report = copy.deepcopy(json.loads(fixture.read_text(encoding='utf-8')))
        for af in ('ipv4', 'ipv6'):
            internal = report['objectives'][f'DUAL-01/{af}']['wire']
            for route in internal.values():
                route['types'] = [0x0602]
            topology = report['objectives'][f'DUAL-04/{af}']['single_path_distances']
            topology['oe']['rd'] = 163840
            external = report['objectives'][f'DUAL-02/{af}']['wire']
            for route in external.values():
                route['types'] = [0x0603]
                for vector in route['metrics']:
                    vector['bandwidth'] = 100000
                    vector['delay'] = 100000000
                    vector['mtu'] = 1500
            for sender in ('oe', 'ce'):
                cycle = report['objectives'][f'DUAL-03/{af}'][sender]
                cycle['changed_types'] = [0x0603]
                for vector in cycle['changed_metrics']:
                    vector['bandwidth'] = 100000
                    vector['delay'] = 1000000000
                    vector['mtu'] = 1500
                # The corrected report must agree with the changed wire vector.
                cycle['after_distance']['rd'] = module.cisco_wide_metric(
                    cycle['changed_metrics'][0])
                cycle['before_distance']['rd'] = cycle['after_distance']['rd'] - 1
                cycle['after_distance']['cd'] = max(cycle['before_distance']['cd'] + 1,
                                                   cycle['after_distance']['rd'] + 1)
        self.assertEqual(module.audit_report(report), [])
        # A report cannot PASS merely because RD increased; its value must
        # equal an actually observed TLV2 vector. MTU is part of seed parity.
        changed = report['objectives']['DUAL-03/ipv4']['oe']['after_distance']
        changed['rd'] += 1
        self.assertTrue(any('changed RD' in f for f in module.audit_report(report)))
        changed['rd'] -= 1
        external = report['objectives']['DUAL-02/ipv6']['wire']['ce']['metrics'][0]
        external['mtu'] = 14419200
        self.assertTrue(any('external seed TLV2 units' in f
                            for f in module.audit_report(report)))


if __name__ == '__main__':
    unittest.main()
